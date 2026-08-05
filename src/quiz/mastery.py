"""Bayesian Knowledge Tracing (BKT) — per-topic mastery estimate updated on every quiz
attempt, stored as a Student-[:MASTERS]->Topic edge (properties: p_know, updated_at) on
the same leaf Topic node that :BELONGS_TO already targets (see src/quiz/attempts.py).

BKT models mastery as a single latent probability p_know: "this student has learned the
skill behind this leaf topic." Unlike :REVIEWING (which schedules WHEN to re-ask a
specific question) or attempt_count (a raw retry counter), p_know is a belief that gets
Bayesian-updated after every observation — a correct answer raises it, a wrong answer
lowers it, but neither pins it at 0 or 1, because BKT models two kinds of noise
explicitly: p_slip (a student who knows the skill can still slip and answer wrong) and
p_guess (a student who doesn't know it can still guess right). A raw correct/incorrect
rate can't separate "actually improving" from "got lucky/unlucky on a couple of
questions"; BKT's guess/slip terms are exactly what let it discount noisy observations
instead of overreacting to them.

Ported directly from notebooks/knowledge_tracing.ipynb (the "BKT baseline" cell) — that
notebook validated this update rule offline (AUC ~0.66 on synthetic data) before this
module wired it into production; this module is now the source of truth going forward,
and the notebook should eventually import from here instead of duplicating the functions.

BKT_PARAMS are global and fixed (not fit per-topic via EM) — deliberately out of scope.
Per-topic EM fitting needs enough attempts per topic to fit reliably and a retraining
pipeline to keep params current; global fixed params give every topic a working (if
generic) mastery estimate from day one. Swapping in per-topic params later only requires
passing a different params dict into bkt_update() — the graph schema and record_attempt()
don't need to change.

On a student's first-ever attempt at a question in a given leaf topic, there is no
:MASTERS edge yet — p_know starts at p_init (0.3), the same "no prior evidence" default
the notebook's run_bkt() uses (state.get(key, params["p_init"])). Every subsequent attempt
reads the current p_know off the existing edge and folds in one new observation. This
makes the update deterministic for a given chronological sequence of attempts, but it is
NOT commutative — BKT is a sequential/recursive update (each step's input is the previous
step's output), so attempts must be applied in chronological order, the same constraint
record_attempt()'s docstring already states for streak/interval when backdating.

Usage:
    from src.quiz.mastery import BKT_PARAMS, bkt_update, mastery_for_student
    new_p_know = bkt_update(current_p_know, correct=True, params=BKT_PARAMS)
    items = mastery_for_student(driver, student_id)
"""

from neo4j import Driver

BKT_PARAMS = dict(p_init=0.3, p_transit=0.1, p_slip=0.1, p_guess=0.25)


def bkt_update(p_know: float, correct: bool, params: dict = BKT_PARAMS) -> float:
    """Bayesian update of P(knows the skill) given one new correct/incorrect observation,
    followed by the "learning" transition step (p_transit: chance of learning the skill
    between this attempt and the next, even if it wasn't known here). Ported verbatim from
    notebooks/knowledge_tracing.ipynb — see module docstring for why this must not diverge
    from that source."""
    p_slip, p_guess, p_transit = params["p_slip"], params["p_guess"], params["p_transit"]
    if correct:
        num = p_know * (1 - p_slip)
        denom = num + (1 - p_know) * p_guess
    else:
        num = p_know * p_slip
        denom = num + (1 - p_know) * (1 - p_guess)
    p_know_post = num / denom if denom > 0 else p_know
    return p_know_post + (1 - p_know_post) * p_transit


def bkt_predict_proba(p_know: float, params: dict = BKT_PARAMS) -> float:
    """P(correct) implied by current p_know, before seeing the observation. Not currently
    called from record_attempt() (which only needs the posterior update), but ported
    alongside bkt_update() since it's part of the same algorithm and is the natural hook
    for a future "predicted difficulty for this student" feature."""
    return p_know * (1 - params["p_slip"]) + (1 - p_know) * params["p_guess"]


_MASTERY_FOR_STUDENT_QUERY = """
MATCH (s:Student {id: $student_id})-[m:MASTERS]->(t:Topic)
RETURN t.path AS topic_path, m.p_know AS p_know, m.updated_at AS updated_at
ORDER BY m.p_know ASC
"""


def mastery_for_student(driver: Driver, student_id: str) -> list[dict]:
    """Returns this student's current p_know per leaf topic they've ever answered a
    question in, weakest topic first — mirrors due_for_review()'s read-back style in
    src/quiz/attempts.py. A topic never attempted has no :MASTERS edge and so never
    appears here, same "no edge yet = no data" convention as :REVIEWING."""
    with driver.session() as session:
        records = session.run(_MASTERY_FOR_STUDENT_QUERY, student_id=student_id)
        return [dict(r) for r in records]
