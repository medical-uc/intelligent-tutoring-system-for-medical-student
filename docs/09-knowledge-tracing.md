# Knowledge Tracing — personalization engine

Prototype, not wired in. [`notebooks/knowledge_tracing.ipynb`](../notebooks/knowledge_tracing.ipynb)
fits a **server-side** Bayesian Knowledge Tracing (BKT) model over the student
interaction graph to answer the actual product question: *for a given student, which
topics are they weak in?* This is the personalization signal the rest of the system
consumes — one `p_know` per (student, topic) pair, so "what should this student study
next" reduces to "sort their topics by `p_know` ascending."

This is exploratory work, evaluated against real dummy-data scale, **not** yet a
replacement for the client-side BKT described in
[06-student-graph.md § Mastery](06-student-graph.md#mastery--masters-not-event-sourced-by-design-trade-off)
and [05-serving-api.md § Quiz router — mastery](05-serving-api.md#quiz-router--mastery).
Today, `p_know` is computed on-device by the frontend and pushed via `PUT /quiz/mastery`;
`src/quiz/mastery.py` does no BKT math server-side. This notebook is a candidate for
moving that computation server-side instead — see "Relationship to `MASTERS`" below.

## Why BKT, not a sequence model (AKT/DKT)

A 2-state (knows / doesn't-know) HMM per (student, leaf topic), updated online per
attempt — cheap, interpretable, and it needs only a handful of attempts per pair to
produce a defensible `p_know`, including graceful cold-start behavior (falls back to the
prior `p_init`) for a new student or a just-published topic.

The notebook checks this assumption against real scale before committing to it (cell 4):
against the dummy dataset (`scripts/generate_dummy_interactions.py`, generated against
the real question bank's subject/topic taxonomy so sequence shape matches what
production data will look like) —

- **10,480 attempts, 43 students, 56 topics**
- median **6 attempts per (student, topic) pair**; 1,509 / 1,554 pairs have ≥5

That's thin per-pair history — nowhere near enough to fit a deep sequence model
(AKT/DKT) per topic without pooling across topics or students, which would blur the
per-student specificity that's the entire point of this feature. A lightweight
few-parameter HMM is the right complexity budget for this data scale; revisit if/when
attempt volume grows an order of magnitude.

## Model — confidence-conditional emissions

Standard BKT has four parameters: `p_init` (prior P(knows)), `p_transit` (P(learns)
between attempts), `p_slip` (P(wrong | knows)), `p_guess` (P(correct | doesn't know)).

Every quiz attempt already carries a self-reported `confidence` label (`confident` /
`unsure` / `guessing` — see
[`src/quiz/attempts.py::record_attempt`](../src/quiz/attempts.py)'s docstring: "a
correct guess and a confident correct answer are not the same evidence of mastery").
Plain BKT throws that signal away and treats every correct/wrong outcome as equally
strong evidence. This model instead makes `p_slip` and `p_guess` **conditional on
reported confidence**, so the same correct/wrong outcome moves `p_know` by a different
amount depending on how the student says they got there:

| Confidence | `p_slip` | `p_guess` | Interpretation |
| --- | --- | --- | --- |
| `confident` | 0.05 | 0.10 | Trusted as real evidence either way — confident-wrong pulls `p_know` down hard as a likely misconception, not a slip. |
| `unsure` | 0.10 | 0.25 | Close to flat/default BKT behavior. |
| `guessing` | 0.30 | 0.50 | Close to a coin flip — both outcomes move `p_know` only a little; a lucky guess shouldn't look like mastery, a wrong guess shouldn't look like a confirmed gap. |

`p_init = 0.3`, `p_transit = 0.1` stay global scalars — only the emission probabilities
vary by confidence. All four are still fixed defaults in this notebook (no per-topic
fitting yet — see "Not yet done" below).

Update rule per attempt (`bkt_update` in the notebook):

```
p_slip, p_guess = P_SLIP[confidence], P_GUESS[confidence]

if observed correct:
    p_know_post = p_know * (1 - p_slip) / (p_know * (1 - p_slip) + (1 - p_know) * p_guess)
else:
    p_know_post = p_know * p_slip / (p_know * p_slip + (1 - p_know) * (1 - p_guess))

p_know_next = p_know_post + (1 - p_know_post) * p_transit
```

Run forward in chronological order per (student, topic) key (`run_bkt`), starting every
unseen pair at `p_init`.

**Confidence-bucket sanity check (cell 8)** — before trusting the conditional-emission
model over a flat one, the notebook confirms raw accuracy actually separates by
confidence bucket on this dataset: `confident` 71.2% correct, `unsure` 53.4%,
`guessing` 32.6% (near chance). The ordering holds, which is the precondition for
confidence-conditional `p_slip`/`p_guess` being a meaningful signal rather than noise.

## Evaluation

Next-step prediction (predict `P(correct)` **before** seeing the observation, then
update — a genuine forward prediction, not fitted in hindsight):

| Metric | Value |
| --- | --- |
| AUC | 0.5658 |
| Log loss | 0.7491 |

Weak (AUC well under the ~0.7+ that'd make this trustworthy as a ranking signal on its
own) — read as a baseline to beat, not a shipped result. Fixed global defaults for
`p_init`/`p_transit` and no per-topic fitting are the likely first place to improve this
(see "Not yet done").

## Per-student weak-topic output — the personalization query

`weakest_topics(student_id, mastery_df, top_n=5, min_observations=3,
stuck_attempt_threshold=2.0)` is the actual product-facing query: this student's
lowest-`p_know` topics, ascending. This is the shape `GET /quiz/mastery` already
returns today (client-computed) and what a server-side equivalent would return.

Each row is annotated with two flags rather than just a bare `p_know` number:

- **`low_evidence`** — fewer than `min_observations` (default 3) attempts. With only 1-2
  attempts, `p_know` is still close to the prior (`p_init`) rather than a real read on
  the student. Rows are **not dropped** — a never-attempted topic is still worth
  surfacing as "unknown, go try it" — just flagged so the caller/UI can render it
  differently ("not enough data yet" vs. a confident weak-topic claim).
- **`stuck`** — enough evidence (not `low_evidence`) but `avg_attempt_count` (from the
  `REVIEWING` edge's lifetime retry counter, see
  [06-student-graph.md](06-student-graph.md#per-question-spaced-repetition--reviewing-quiz-side))
  is ≥ `stuck_attempt_threshold` (default 2.0). This student has repeatedly re-attempted
  these questions and still hasn't built up `p_know` — reads differently from a topic
  that's merely weak on first exposure, and worth a different UI treatment ("revisit
  with a different approach" vs. "just practice more").

Worked example from the notebook (student `05bbbea4...`), 3 weakest topics all
independently confirmed by raw accuracy on the same underlying attempts:

| Topic | `p_know` | Raw accuracy | n |
| --- | --- | --- | --- |
| Thyroid > Thyroid hormone synthesis | 0.111 | 0.50 | 6 |
| Histology - Digestive 1 (2026) > Liver functions and structure | 0.124 | 0.167 | 6 |
| Anatomy of Neck > Cervical nerve anatomy | ~0.13 | 0.20 | 20 |

## Relationship to `MASTERS` and the client-side BKT question

[06-student-graph.md](06-student-graph.md#mastery--masters-not-event-sourced-by-design-trade-off)
flags a genuinely open question: `p_know` today arrives pre-computed from the client and
is persisted server-side with zero validation, recomputation, or history — a departure
from this project's general principle that mastery-like state should be **derived**, not
asserted. This notebook is the concrete first step toward resolving that in the
"server derives it" direction: if adopted, `p_know` would be computed here (or in a
`src/quiz/knowledge_tracing.py` module following this notebook's logic) directly from
`QUIZ_ANSWER`/`REVIEWING` event history already in Neo4j, rather than trusted from the
client. That would make `PUT /quiz/mastery` unnecessary and turn `GET /quiz/mastery`
into a genuinely derived read, consistent with `REVIEWING` and `Flashcard`'s existing
event-sourced pattern.

Not resolved by this notebook alone — still a decision for whoever owns the BKT
client/product surface, same as flagged in 06/HANDOVER. What this notebook adds to that
decision: evidence that a server-side version is cheap to compute (per-attempt online
update, no training loop) and cheap to serve (one HMM state per active (student, topic)
pair), so "server can't feasibly do this" is not a reason to keep it client-side.

## Data source

`scripts/generate_dummy_interactions.py` — synthetic students generated against the real
question bank's subject/topic taxonomy
([`notebooks/mcq_output/question_bank.json`](../notebooks/mcq_output/question_bank.json)),
so topic-level sequence shape matches production data. Requires `make neo4j-up` and the
dummy data already populated — see [07-operations.md](07-operations.md#local-setup) or
this repo's README for the fast-path setup. Query pulled in cell 3
(`notebooks/knowledge_tracing.ipynb`): one row per `QUIZ_ANSWER`, joined to its leaf
`Topic` via `(:Question)-[:BELONGS_TO]->(:Topic)`, ordered by `ts` within each student,
left-joined to the `REVIEWING` edge's `attempt_count` for the stuck-topic signal.

## Not yet done

- **Parameter fitting.** `p_init`/`p_transit`/`p_slip`/`p_guess` are hand-picked
  defaults, not fit from data (standard BKT fitting is EM/gradient-based per skill).
  Likely the biggest lever on the current 0.5658 AUC.
- **No integration.** Not called from `src/`, `app/`, or any script — the notebook is
  the only place this logic exists. No `MASTERS`-write path, no API endpoint.
- **No per-topic parameter variation.** Every topic shares one global
  `p_init`/`p_transit`/`p_slip`/`p_guess` set; topics plausibly differ in intrinsic
  difficulty and guessability (more answer options, more conceptually adjacent
  distractors, etc.) in ways a single global prior can't capture.
- **Confidence at predict-time.** `bkt_predict_proba` needs `confidence` as an input,
  which is only known in hindsight from logged data. A live recommender (e.g. "what's
  P(correct) if I show this student this question right now") doesn't know the
  student's confidence before they answer — the notebook's own docstring flags this and
  falls back to the `unsure` (middle) row for that use case; not yet exercised end to
  end.
