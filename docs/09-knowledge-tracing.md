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
vary by confidence. These four were the original hand-picked defaults; the notebook now
also EM-fits all four from data (see "Parameter fitting — EM (Baum-Welch)" below), and
that fitted set is what section 3 (`mastery_df`) and the weakest-topics example run on.

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

## Parameter fitting — EM (Baum-Welch)

The hand-picked defaults above (`p_init=0.3`, `p_transit=0.1`, and the `p_slip`/`p_guess`
table) were never fit to data — they were the AUC ceiling, not the model's. The notebook
now fits all four by Baum-Welch EM (`fit_bkt_em`, section 2b): standard forward-backward
E-step, closed-form 2-state M-step, run to convergence (14 iterations on the current
dataset).

**Fit globally, not per-topic.** Per-topic EM needs roughly the 25-students-per-skill
floor from Slater & Baker (2018, *Behaviormetrika*) to converge reliably; the current
scale (43 students spread across 56 topics) is well under that per-topic, so a per-topic
fit would reproduce the exact degenerate-parameter failure mode that paper measured.
Instead, one shared parameter set is fit jointly across every (student, topic) sequence
pooled together — genuinely joint (aggregating expected counts across all sequences each
M-step), not per-sequence fits averaged after the fact. Per-topic fitting is future work,
gated on attempt volume growing past that floor.

**E-step.** For each (student, topic) sequence, run forward-backward over the 2-state
chain (state 0 = doesn't-know, state 1 = knows) with a one-directional transition matrix
(no forgetting — state 1 → 0 has zero probability, matching the original online-update
model):

```
trans = [[1 - p_transit, p_transit],
         [0,             1         ]]

emission(correct, confidence, state):
    if state == knows:      P = (1 - p_slip[confidence]) if correct else p_slip[confidence]
    if state == doesn't-know: P = p_guess[confidence]      if correct else (1 - p_guess[confidence])
```

Forward-backward (with the standard per-timestep rescaling for numerical stability)
yields, per attempt: `gamma[t]` = P(state at t | full sequence) and `xi01[t]` = P(state
t = doesn't-know, state t+1 = knows | full sequence) — the soft, sequence-aware version
of "was this attempt made while knowing or not," using the whole sequence's evidence,
not just what came before.

**M-step** (closed form, aggregated across every sequence before updating):

```
p_init     = mean over sequences of gamma[0, knows]
p_transit  = sum(xi01) / sum(gamma[:-1, doesn't-know])

for each confidence bucket c:
    p_slip[c]  = 1 - ( sum of gamma[t, knows]      where correct, conf==c
                        / sum of gamma[t, knows]      where conf==c )
    p_guess[c] = ( sum of gamma[t, doesn't-know] where correct, conf==c
                    / sum of gamma[t, doesn't-know] where conf==c )
```

Repeat E/M until total log-likelihood change falls below tolerance (or `n_iter` cap).

**Fitted result** (current dataset, starting from the hand-picked defaults as the EM
initialization):

| Param | Hand-picked | EM-fitted |
| --- | --- | --- |
| `p_init` | 0.30 | 0.403 |
| `p_transit` | 0.10 | 0.055 |
| `p_slip[confident]` | 0.05 | 0.177 |
| `p_slip[unsure]` | 0.10 | 0.419 |
| `p_slip[guessing]` | 0.30 | 0.535 |
| `p_guess[confident]` | 0.10 | 0.553 |
| `p_guess[unsure]` | 0.25 | 0.487 |
| `p_guess[guessing]` | 0.50 | 0.230 |

**Read `p_guess[confident]` (0.553) > `p_guess[guessing]` (0.230) as a finding, not a
bug.** It inverts the hand-picked intuition that a confident answer should rarely be a
lucky guess. EM isn't wrong here — it's reporting that the two hidden states aren't
fully separable from confidence alone on this data: the `confident` bucket runs high
accuracy overall (71.2%, see the sanity check above), so even in the timesteps EM
assigns to "doesn't know," a `confident`-labeled attempt is still often correct, which
pulls `p_guess[confident]` up. Treat the fitted `p_slip`/`p_guess` values as what best
explains the observed correctness sequences, not as a re-confirmation of the original
confidence-conditional design intuition — that's a separate claim EM doesn't test.

## Evaluation

Next-step prediction (predict `P(correct)` **before** seeing the observation, then
update — a genuine forward prediction, not fitted in hindsight):

| Params | AUC | Log loss |
| --- | --- | --- |
| Hand-picked (original defaults) | 0.5658 | 0.7491 |
| EM-fitted (Baum-Welch, see above) | 0.6819 | 0.6404 |

EM fitting alone moves AUC from barely-above-chance to a usable-but-not-great ranking
signal — confirms the hand-picked defaults, not BKT's 2-state structure itself, were the
ceiling. Still short of the ~0.7+ that'd make this fully trustworthy standalone; read as
progress against the baseline, not a shipped result. Section 3 (`mastery_df` and the
weakest-topics example) runs on the EM-fitted params. Per-topic parameter variation
(gated on attempt volume, see "Parameter fitting" above) is the next lever.

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

- **No integration.** Not called from `src/`, `app/`, or any script — the notebook is
  the only place this logic exists. No `MASTERS`-write path, no API endpoint, and no
  persistence of `BKT_PARAMS_FITTED` outside the notebook run that produced it.
- **No per-topic parameter variation.** EM fitting is global (one shared parameter set
  across every topic) — see "Parameter fitting" above for why: current scale (43
  students / 56 topics) is under the ~25-students-per-skill floor a per-topic fit would
  need to converge reliably (Slater & Baker 2018). Topics plausibly differ in intrinsic
  difficulty and guessability in ways a single global prior can't capture; revisit once
  attempt volume grows past that floor.
- **Confidence/state separability.** The EM-fitted `p_guess[confident]` >
  `p_guess[guessing]` inversion (see "Parameter fitting" above) suggests confidence
  alone doesn't fully separate the two hidden states on this data. Worth a follow-up
  look at whether a different feature (e.g. response time, also in the synthetic
  generator) separates them better.
- **Confidence at predict-time.** `bkt_predict_proba` needs `confidence` as an input,
  which is only known in hindsight from logged data. A live recommender (e.g. "what's
  P(correct) if I show this student this question right now") doesn't know the
  student's confidence before they answer — the notebook's own docstring flags this and
  falls back to the `unsure` (middle) row for that use case; not yet exercised end to
  end.
- **EM initialization sensitivity.** The fit above starts from the hand-picked defaults
  as its initialization; not yet checked whether EM converges to the same fixed point
  from other starting points (a standard EM local-optima risk, unverified here).
