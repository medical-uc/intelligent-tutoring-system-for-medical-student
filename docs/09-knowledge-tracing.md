# Knowledge Tracing — personalization engine

[`notebooks/knowledge_tracing.ipynb`](../notebooks/knowledge_tracing.ipynb) fits a
Bayesian Knowledge Tracing (BKT) model over the student interaction graph to answer the
actual product question: *for a given student, which topics are they weak in?* This is
the personalization signal the rest of the system consumes — one `p_know` per (student,
topic) pair, so "what should this student study next" reduces to "sort their topics by
`p_know` ascending."

The split between what's server-side and what's client-side is deliberate, and narrower
than "the notebook's model runs on the server":

- **`p_know` itself** — the personalized, per-(student, topic) mastery estimate — is
  computed **on-device only** (the iOS app's `BKTStore.swift`), same as described in
  [06-student-graph.md § Mastery](06-student-graph.md#mastery--masters-not-event-sourced-by-design-trade-off)
  and [05-serving-api.md § Quiz router — mastery](05-serving-api.md#quiz-router--mastery).
  Computed once per graded attempt, pushed to the server via `PUT /quiz/mastery`, and
  `src/quiz/mastery.py` still does no `p_know` math server-side — this notebook doesn't
  change that.
- **The shared BKT parameters** (`p_init`, `p_transit`, `p_slip`, `p_guess`) that
  `p_know`'s update rule runs on — a population-level property ("how noisy is a
  confident vs. a guessing answer," pooled across every student), not a per-student one
  — **are** now fit server-side, from real data, and served to clients. This notebook is
  where that fit was developed (see "Parameter fitting — EM (Baum-Welch)" below);
  `src/quiz/bkt_fit.py` is the productionized version of the same code, exposed via
  `GET /quiz/mastery/params` and fetched/cached by `BKTStore.swift`. See "Relationship
  to `MASTERS`" below for why this split doesn't reintroduce server-side `p_know`
  computation.

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

## Keeping params fresh

Two different "update" concerns here, don't conflate them:

- **Per-attempt `p_know`.** Fully online, always was — no batch job involved.
  `BKTStore.record` (iOS) / `bkt_update` (notebook) takes the current `p_know` plus one
  new `(correct, confidence)` observation and returns the next `p_know`, O(1), no replay
  of history. This is the whole point of choosing BKT (see "Why BKT" above) — every new
  interaction updates `p_know` for its (student, topic) pair on its own.
- **The fitted BKT parameters** (`p_init`/`p_transit`/`p_slip`/`p_guess`). These *do*
  need periodic refitting as real interaction volume grows — a batch fit over full
  sequences (forward-backward needs the whole sequence, not one new point), not
  something that updates per-attempt.

**Production path: live, server-side, self-throttled — no manual step.**
`src/quiz/bkt_fit.py::get_fitted_params` refits directly from Neo4j on request, cached
in-process for 6h (`_CACHE_TTL_SECONDS`) so concurrent app launches/syncs don't each
trigger a fresh query + EM run. Served via `GET /quiz/mastery/params`. The iOS client
(`BKTStore.refreshParamsIfNeeded`, called from the dashboard's load path) fetches at
most once per 6h itself, caches the result in `UserDefaults`, and falls back to the
original hand-picked constants if it has never successfully fetched (e.g. first launch
while offline) — so a device is never blocked on this call, only ever running on
possibly-stale-but-always-valid params.

Below `MIN_SEQUENCES_TO_FIT` (100 (student, topic) sequences), `get_fitted_params`
returns the hand-picked defaults unfit rather than risking the degenerate-parameter
failure mode a too-small EM fit produces (`fitted_from_defaults: true` in the response)
— same reasoning as the "Fit globally, not per-topic" call below, applied to the
overall-too-little-data case.

**This notebook's role now:** development/validation environment for the fitting
code, not the only place it runs. `fit_bkt_em`/`_forward_backward` here and in
`src/quiz/bkt_fit.py` are kept in lockstep by construction (the backend module was
ported directly from this notebook's section 2b) — if the algorithm changes, change it
here first, validate the AUC/log-loss impact (see "Evaluation" below), then port to
`src/quiz/bkt_fit.py`. `notebooks/bkt_params_fitted.json` (written by the persist cell
below) is a point-in-time snapshot for offline inspection/diffing, not something the
running server reads — the server always fits fresh (subject to its own cache) rather
than loading this file.

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
asserted. **Still open** — the params-fitting endpoint below does not resolve it.

It's tempting to read "the server now computes something BKT-related" as progress
toward "server derives `p_know`," but it's a narrower move than that: `GET
/quiz/mastery/params` serves the shared emission/transition *parameters*
(population-level — pooled across every student, refit periodically), not `p_know`
itself. `p_know` is still entirely client-computed and client-asserted via `PUT
/quiz/mastery`, with the same zero server-side validation/recomputation 06-student-graph
flags. A student's device could push any `p_know` it wants and the server would still
accept it verbatim — that hasn't changed.

If "server derives `p_know`" is adopted later, it would still need `p_know` computed
directly from `QUIZ_ANSWER`/`REVIEWING` event history in Neo4j (the way
`src/quiz/bkt_fit.py` already reads that same history for the params fit), and would
make `PUT /quiz/mastery` unnecessary. What *is* now resolved: "server can't feasibly do
BKT-shaped computation" is no longer a reason to keep `p_know` client-side either —
`src/quiz/bkt_fit.py` demonstrates the full sequence pull + forward-backward + EM loop
runs in about a second server-side (see "Keeping params fresh" above), and per-attempt
`p_know` update is far cheaper than that (O(1), no batch step at all). The remaining
question is still a product/ownership one — who owns the BKT client/product surface —
not a technical feasibility one.

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

- **Per-process cache, not shared.** `src/quiz/bkt_fit.py`'s `get_fitted_params` cache
  is an in-process module-level variable. If the API runs as multiple worker
  processes, each worker fits and caches independently — not incorrect (every worker
  eventually converges to the same fit from the same data), but wasteful (N workers = N
  redundant EM runs on cache expiry instead of one shared fit) and means workers can
  briefly disagree on params right after a restart. A shared cache (Redis, or a
  `written-by-a-cron-job` row in Postgres/Neo4j) would fix both; not needed at current
  scale/worker count.
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
