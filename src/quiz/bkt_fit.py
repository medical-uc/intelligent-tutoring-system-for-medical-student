"""EM-fits the global BKT emission/transition parameters (p_init, p_transit, p_slip,
p_guess) from real QUIZ_ANSWER history, so the client's on-device BKT (see the iOS app's
BKTStore.swift) runs on parameters that reflect actual student data instead of the
originally hand-picked defaults.

This is deliberately narrower than the p_know computation itself: p_know stays
client-computed per src/quiz/mastery.py's design (the client is the source of truth for
that, and for the personalization it produces). What's fit here is the *shared*
emission/transition model — how noisy a "confident" vs. "guessing" answer actually is,
pooled across every student and topic — which is a population-level property, not a
per-student one, so serving it from the server doesn't reintroduce server-side p_know
computation.

Ported line-for-line from notebooks/knowledge_tracing.ipynb's "Parameter fitting — EM
(Baum-Welch)" section (`fit_bkt_em`) — see that notebook for the derivation and the
Slater & Baker (2018) citation for why this fits one global parameter set rather than
one per topic (current data scale is well under the ~25-students-per-skill floor a
per-topic fit would need to converge).

Usage:
    from src.quiz.bkt_fit import get_fitted_params
    params = get_fitted_params(driver)  # cached, see _CACHE_TTL_SECONDS
"""

import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from neo4j import Driver

CONFIDENCE_LEVELS = ("confident", "unsure", "guessing")

# Same values notebooks/knowledge_tracing.ipynb's BKT_PARAMS starts EM from — used here
# only as the EM initialization point and as a cold-start fallback when there isn't
# enough data yet to fit (see get_fitted_params).
DEFAULT_PARAMS = {
    "p_init": 0.3,
    "p_transit": 0.1,
    "p_slip": {"confident": 0.05, "unsure": 0.10, "guessing": 0.30},
    "p_guess": {"confident": 0.10, "unsure": 0.25, "guessing": 0.50},
}

# Below this many (student, topic) sequences, EM is skipped in favor of DEFAULT_PARAMS —
# matches the notebook's data-scale reasoning: too few sequences risks the
# degenerate-parameter failure mode Slater & Baker (2018) measured, worse than not
# fitting at all.
MIN_SEQUENCES_TO_FIT = 100

_CACHE_TTL_SECONDS = 6 * 60 * 60  # refit at most every 6h; see FittedBKTParams caching

_SEQUENCES_QUERY = """
MATCH (s:Student)-[:ATTEMPTED]->(:QuizSession)-[:HAS_ANSWER]->(e:InteractionEvent {type: "QUIZ_ANSWER"})
MATCH (e)-[:FOR_QUESTION]->(q:Question)-[:BELONGS_TO]->(t:Topic)
RETURN s.id AS student_id, t.path AS topic_path, e.correct AS correct,
       e.confidence AS confidence, e.ts AS ts
ORDER BY s.id, e.ts ASC
"""


@dataclass
class FittedBKTParams:
    p_init: float
    p_transit: float
    p_slip: dict[str, float]
    p_guess: dict[str, float]
    n_attempts: int
    n_sequences: int
    em_iterations: int
    fitted_from_defaults: bool = field(
        default=False,
        metadata={"doc": "True if too little data existed to fit and DEFAULT_PARAMS was returned as-is."},
    )


def _build_sequences(df: pd.DataFrame) -> list[list[tuple[int, str]]]:
    """One (correct, confidence) list per (student, topic) pair, in chronological order —
    the unit Baum-Welch runs forward-backward over."""
    sequences = []
    for _, group in df.groupby(["student_id", "topic_path"], sort=False):
        sequences.append(list(zip(group["correct"].astype(int), group["confidence"])))
    return sequences


def _emission_prob(correct: int, confidence: str, state: int, params: dict) -> float:
    """P(observed correct | hidden state), state 0 = doesn't-know, state 1 = knows."""
    p_slip = params["p_slip"][confidence]
    p_guess = params["p_guess"][confidence]
    if state == 1:
        return (1 - p_slip) if correct else p_slip
    return p_guess if correct else (1 - p_guess)


def _forward_backward(seq: list[tuple[int, str]], params: dict):
    """Standard 2-state forward-backward. Transition matrix is the BKT one-directional
    learning assumption (state 0 -> 1 w.p. p_transit, no forgetting: 1 -> 1 always)."""
    T = len(seq)
    p_init, p_transit = params["p_init"], params["p_transit"]
    trans = np.array([[1 - p_transit, p_transit], [0.0, 1.0]])

    emit = np.empty((T, 2))
    for t, (correct, conf) in enumerate(seq):
        emit[t, 0] = _emission_prob(correct, conf, 0, params)
        emit[t, 1] = _emission_prob(correct, conf, 1, params)

    alpha = np.empty((T, 2))
    scale = np.empty(T)
    alpha[0] = np.array([1 - p_init, p_init]) * emit[0]
    scale[0] = alpha[0].sum()
    alpha[0] /= scale[0]
    for t in range(1, T):
        alpha[t] = (alpha[t - 1] @ trans) * emit[t]
        scale[t] = alpha[t].sum()
        alpha[t] /= scale[t]

    beta = np.empty((T, 2))
    beta[T - 1] = 1.0
    for t in range(T - 2, -1, -1):
        beta[t] = (trans @ (emit[t + 1] * beta[t + 1])) / scale[t + 1]

    gamma = alpha * beta
    gamma /= gamma.sum(axis=1, keepdims=True)

    # xi01[t] = P(state_t=0, state_{t+1}=1 | obs) — the only nonzero transition besides
    # self-loops, since state 1 -> 0 has zero probability by construction.
    xi01 = np.empty(T - 1)
    for t in range(T - 1):
        xi01[t] = (
            alpha[t, 0] * p_transit * emit[t + 1, 1] * beta[t + 1, 1]
        ) / scale[t + 1]

    loglik = np.log(scale).sum()
    return gamma, xi01, loglik


def fit_bkt_em(
    sequences: list[list[tuple[int, str]]],
    init_params: dict,
    n_iter: int = 30,
    tol: float = 1e-4,
) -> tuple[dict, list[float]]:
    """Baum-Welch EM, pooled across all sequences (one shared param set). Each M-step
    aggregates expected counts over every sequence before updating params, so this is a
    genuine joint fit, not per-sequence fits averaged after the fact."""
    params = {
        "p_init": init_params["p_init"],
        "p_transit": init_params["p_transit"],
        "p_slip": dict(init_params["p_slip"]),
        "p_guess": dict(init_params["p_guess"]),
    }
    history = []

    for it in range(n_iter):
        init_num = 0.0
        trans_num, trans_denom = 0.0, 0.0
        slip_num = {c: 0.0 for c in CONFIDENCE_LEVELS}
        slip_denom = {c: 0.0 for c in CONFIDENCE_LEVELS}
        guess_num = {c: 0.0 for c in CONFIDENCE_LEVELS}
        guess_denom = {c: 0.0 for c in CONFIDENCE_LEVELS}
        total_loglik = 0.0

        for seq in sequences:
            gamma, xi01, loglik = _forward_backward(seq, params)
            total_loglik += loglik

            init_num += gamma[0, 1]
            trans_num += xi01.sum()
            trans_denom += gamma[:-1, 0].sum()

            for t, (correct, conf) in enumerate(seq):
                p_know_state, p_unknow_state = gamma[t, 1], gamma[t, 0]
                if correct:
                    slip_num[conf] += p_know_state
                    guess_num[conf] += p_unknow_state
                slip_denom[conf] += p_know_state
                guess_denom[conf] += p_unknow_state

        n_seq = len(sequences)
        params["p_init"] = float(np.clip(init_num / n_seq, 1e-3, 1 - 1e-3))
        params["p_transit"] = float(
            np.clip(
                trans_num / trans_denom if trans_denom > 0 else params["p_transit"],
                1e-3,
                1 - 1e-3,
            )
        )
        for c in CONFIDENCE_LEVELS:
            correct_given_know = (
                slip_num[c] / slip_denom[c] if slip_denom[c] > 0 else (1 - params["p_slip"][c])
            )
            params["p_slip"][c] = float(np.clip(1 - correct_given_know, 1e-3, 1 - 1e-3))
            params["p_guess"][c] = float(
                np.clip(
                    guess_num[c] / guess_denom[c] if guess_denom[c] > 0 else params["p_guess"][c],
                    1e-3,
                    1 - 1e-3,
                )
            )

        history.append(total_loglik)
        if it > 0 and abs(history[-1] - history[-2]) < tol * abs(history[-2]):
            break

    return params, history


def fit_from_neo4j(driver: Driver) -> FittedBKTParams:
    """Pulls every QUIZ_ANSWER sequence from Neo4j and EM-fits global BKT params from
    them. Falls back to DEFAULT_PARAMS (unfit) if there's too little data to trust a fit
    — see MIN_SEQUENCES_TO_FIT."""
    with driver.session() as session:
        records = [dict(r) for r in session.run(_SEQUENCES_QUERY)]

    if not records:
        return FittedBKTParams(
            **DEFAULT_PARAMS,
            n_attempts=0,
            n_sequences=0,
            em_iterations=0,
            fitted_from_defaults=True,
        )

    df = pd.DataFrame(records)
    df["correct"] = df["correct"].astype(int)
    df["confidence"] = df["confidence"].fillna("unsure")
    sequences = _build_sequences(df)

    if len(sequences) < MIN_SEQUENCES_TO_FIT:
        return FittedBKTParams(
            **DEFAULT_PARAMS,
            n_attempts=len(df),
            n_sequences=len(sequences),
            em_iterations=0,
            fitted_from_defaults=True,
        )

    fitted, history = fit_bkt_em(sequences, DEFAULT_PARAMS)
    return FittedBKTParams(
        p_init=fitted["p_init"],
        p_transit=fitted["p_transit"],
        p_slip=fitted["p_slip"],
        p_guess=fitted["p_guess"],
        n_attempts=len(df),
        n_sequences=len(sequences),
        em_iterations=len(history),
        fitted_from_defaults=False,
    )


_cache: FittedBKTParams | None = None
_cache_fitted_at: float = 0.0


def get_fitted_params(driver: Driver) -> FittedBKTParams:
    """Same as fit_from_neo4j, but cached in-process for _CACHE_TTL_SECONDS so concurrent
    requests (e.g. many devices syncing around the same time) don't each trigger a fresh
    Neo4j query + EM run. Cache resets on process restart — the next request after a
    restart pays the ~1s fit cost once, subsequent requests within the TTL are free."""
    global _cache, _cache_fitted_at
    now = time.monotonic()
    if _cache is None or (now - _cache_fitted_at) > _CACHE_TTL_SECONDS:
        _cache = fit_from_neo4j(driver)
        _cache_fitted_at = now
    return _cache
