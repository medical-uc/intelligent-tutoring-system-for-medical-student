# Project Handover — Intelligent Tutoring System for Medical Student

Date: 2026-08-14
Branch at handover: `feat/textbook-parser` (8 commits ahead of `main`, not yet merged, clean tree)
Prior docs: [01-architecture.md](01-architecture.md) → [09-knowledge-tracing.md](09-knowledge-tracing.md) —
detailed, kept in sync as of this handover. This handover layers on top and flags what's
changed or missing since, rather than repeating them.

**Since the 2026-08-12 revision of this handover:** [10-frontend-integration.md](10-frontend-integration.md)
was added — full request-by-request sequence diagrams for every major iOS client flow
(app launch/auth, dashboard load, quiz answer loop, flashcard review loop, mastery sync,
session expiry), traced directly from the client source in the sibling
`personalized-medical-learning-ios` repo. No backend behavior changed; this closes a
documentation gap (the API docs previously described endpoint shape but not the actual
client call sequences around them, e.g. the on-device BKT step and the double
`PUT /quiz/mastery` push that happen around `/check` and `/log`).

**Since the 2026-08-11 revision of this handover:** commit `2892222` ported a standalone
`domain_kg` staged knowledge-graph pipeline (stages 1-8: parse → NER/relation-extract →
UMLS-link → post-coordinate → image-caption-link → RDF-assert → OWL-RL-reason → serve)
into `src/domain_kg/`. Full detail: [08-domain-kg-pipeline.md](08-domain-kg-pipeline.md).
This is a **new, separate, not-yet-wired-in** subsystem — it does not replace anything
described below, and none of the "not yet decided" items from the prior revision were
resolved by it (if anything it adds one: see §9).

---

## 0. How to run the project

Full detail (all `make` targets, env vars, test running): [07-operations.md](07-operations.md).
This is the fast path.

**Setup (once):**

```bash
cp .env.example .env        # or `make setup`, which every other make target depends on
uv sync                     # main .venv — everything except MinerU and domain_kg Stage 2/3
```

`.venv-mineru` (MinerU only) needs its own separate install per MinerU's docs — not in
`pyproject.toml` by design. See §4 below and [08-domain-kg-pipeline.md](08-domain-kg-pipeline.md)
if you also need `domain_kg` Stage 2/3 (a third env, not yet documented as a recipe).

**Run the live app (the thing that actually serves students):**

```bash
make neo4j-up                              # student graph — minimum to run the API
.venv/bin/uvicorn app.main:app --reload    # or: make server
```

`GET /health` should respond once this is up.

**Populate content the app reads from** (empty Neo4j/Postgres otherwise mean empty
quiz/flashcard endpoints):

```bash
make mlflow-up          # brings up Postgres (despite the name — see §3/§9 re: MLflow itself unused)
make populate-postgres  # question_bank -> mcq_questions table, what the API actually reads
make populate-neo4j     # domain ontology graph -> Neo4j (separate from student graph)
make populate-students  # optional: seeded synthetic demo students/activity
# or just: make populate   (all three population targets in sequence)
```

**Regenerate content from scratch** (PDF → domain KG → question bank, see
[08-domain-kg-pipeline.md](08-domain-kg-pipeline.md)):

```bash
python -m src.domain_kg.cli.run                                  # PDFs -> KG docs -> questions.json per chapter
.venv/bin/python scripts/populate_biochem_mcq_postgres.py         # -> live Postgres table
```

(Superseded: `notebooks/mcq_generation.ipynb` + `scripts/populate_mcq_postgres.py` were
the anatomy/endocrine-era pipeline — question bank is biochem-only now, see §2. Do not
run `populate_mcq_postgres.py`; it's kept only as a superseded-artifact reference.)

**Run the knowledge-tracing notebook** (BKT param EM-fitting — same fit the
`GET /quiz/mastery/params` endpoint runs live in production; this notebook is the
dev/validation environment for that code, not the only place it runs — see
[09-knowledge-tracing.md](09-knowledge-tracing.md)):

```bash
make neo4j-up            # if not already up
make populate-students   # scripts/generate_dummy_interactions.py -> synthetic attempts
# then run notebooks/knowledge_tracing.ipynb end-to-end
```

**Run tests:** `.venv/bin/pytest` (no CI runs this automatically yet — see §5/§9).

**Tear down:** `make down` (stop stack, keep data) or `make clean` (stop stack **and
destroy volumes** — Postgres + Neo4j + MinIO data gone; confirm before running).

**`src/domain_kg/` is now part of the run path** (as of `07050b5`/`a0a7438`): its CLI
(`python -m src.domain_kg.cli.run`, § above) produces each chapter's `questions.json`,
which `scripts/populate_biochem_mcq_postgres.py` loads into the live `mcq_questions`
table — the quiz bank is biochem-only, sourced entirely from this pipeline now, not
invoked standalone/unused as prior versions of this doc said.

The knowledge-tracing notebook above is different: its BKT param-fitting logic (not
`p_know` itself) *is* wired into the live app — `src/quiz/bkt_fit.py` runs the same EM
fit on request, served via `GET /quiz/mastery/params`. `p_know` still isn't written back
to Neo4j server-side and the live app still gets mastery from client-pushed `PUT
/quiz/mastery` (§6) — that part of the design is unchanged.

---

## 1. Project overview

**Problem statement / goal.** Tutoring system for medical students. Two loosely-coupled
halves: an offline content pipeline that turns PDF lecture slides into a bank of
multiple-choice questions and flashcards, and a live FastAPI service that serves that
content to students and tracks their learning (answers, streaks, mastery, spaced
repetition) in a Neo4j graph. A native iOS app (`personalized-medical-learning-ios`, a
**separate sibling repo**, not part of this one) is the actual client — see
[10-frontend-integration.md](10-frontend-integration.md) for full request sequences
against every endpoint this repo serves. No formal problem statement / success-metric
document found in-repo (no `docs/00-*` or product brief) — get the original problem
framing and target success criteria (e.g. target learning-outcome improvement, adoption
numbers) directly from the outgoing owner; not derivable from code.

**Current status.** Prototype / active development, not production-hardened:
- No deployment config in-repo (§5).
- No CI (§4).
- MLflow experiment-tracking stack deployed but never called (§3, §9).
- Two experimental PDF-parser notebooks (olmOCR, Infinity-Parser2-Pro) added on the
  current branch, not wired into the production ingestion path yet — this branch name
  (`feat/textbook-parser`) suggests that's the active work in flight.

**What "done" looks like.** Not documented explicitly anywhere in-repo. Infer from
architecture: a PDF goes in, a reviewed question/flashcard bank comes out, and a student
can register, answer questions, review flashcards, and see mastery/streak progress
through the API. Confirm actual acceptance criteria with the outgoing owner or
stakeholder before treating this as a spec.

---

## 2. Data

**Sources.** `data/textbook/` (e.g. Ganong's Review of Medical Physiology 27th ed.,
Harper's Illustrated Biochemistry, Junqueira) and `data/mcq/` (pre-made question packets,
`Paket soal/Batch 1`, `Batch 2`). Entire `data/` directory is **gitignored** — not in the
repo, lives only on whoever's machine ran ingestion. No documented source-of-truth
location (shared drive, bucket) for these PDFs was found — ask the outgoing owner where
the canonical copies live, since losing local `data/` currently means losing the raw
inputs entirely.

**Licensing.** Not addressed anywhere in-repo. These are named commercial/academic
textbooks — if this project is used beyond internal prototyping, confirm licensing terms
for storing/processing textbook PDFs before wider distribution. Flagging this explicitly
since it's a real risk, not a code gap.

**Labeling / collection process.** No manual labeling step — questions are LLM-generated
(see §3) with an automated self-critique/correction pass, not human-annotated. The
closest thing to "ground truth" is the source PDF text itself.

**Preprocessing / feature engineering — versioned, not prose:**
- `scripts/ingest_data.py` — production entry point, PDF → `_v1_chunks.json`. Runs
  MinerU (subprocess, isolated `.venv-mineru`) → `src/post_process/pipeline.py`'s
  8 deterministic rule-based stages → `src/captioning/qwen_vl.py` (Qwen2.5-VL captioning)
  → `src/ingestion/unify.py` → `src/ingestion/semantic_chunk.py` (sentence-transformers
  `all-MiniLM-L6-v2` embeddings, adaptive-percentile chunk splitting). Full stage table:
  [03-ingestion-pipeline.md](03-ingestion-pipeline.md).
- `src/domain_kg/mcq.py` (via `python -m src.domain_kg.cli.run`) — generates each
  chapter's `questions.json` (`src/domain_kg/data/docs/<chapter>/questions.json`)
  straight from that chapter's KG extraction. Full detail:
  [08-domain-kg-pipeline.md](08-domain-kg-pipeline.md).
- `scripts/populate_biochem_mcq_postgres.py` — converts every chapter's
  `questions.json` into rows for the `mcq_questions` Postgres table the live app
  actually reads from (keyed `"{chapter_dir}::{item_id}"`; upserts in place on
  rerun). This is the only populator that should be run now — the quiz bank is
  biochem-only as of `07050b5`/`a0a7438`.
- **Superseded:** `notebooks/mcq_generation.ipynb` (chunks → `question_bank.json`,
  6-step LLM pipeline) and `scripts/populate_mcq_postgres.py` (loaded
  `notebooks/master_mcq_with_topics.json`) were the anatomy/endocrine-era pipeline.
  `question_bank.json` and `master_mcq_with_topics.json` are now stale artifacts —
  don't treat them as current data. Kept in-repo per convention of flagging
  superseded artifacts rather than silently removing them; the quality-issues
  paragraph below describes that old dataset, not the live one.

**Known data quality issues:**
- **116 of 188 questions (~62%) in `question_bank.json` have `valid_as_generated: false`**
  — meaning they needed a self-critique correction pass. That field doesn't carry through
  to `master_mcq_with_topics.json` (what's actually served), so this quality signal is
  currently **not applied** to the live question set. See
  [04-mcq-generation.md](04-mcq-generation.md) for exact schema detail — worth deciding
  whether to propagate this filter before it's forgotten.
- No documented class-imbalance or drift analysis for question difficulty tiers or topic
  coverage — `src/evaluation/mineru_quality.py` exists as a reference-free *extraction*
  quality scorer (garbage-char ratio, block confidence) but nothing evaluates
  question-bank-level distribution or drift.
- `data/mcq/mcqs.json` is a stale prototype format, nothing reads it — candidate for
  deletion (kept per repo convention of flagging superseded artifacts rather than
  silently removing them).

**Regenerating a dataset snapshot:**
```bash
python -m src.domain_kg.cli.run                             # PDFs → KG docs → questions.json per chapter
.venv/bin/python scripts/populate_biochem_mcq_postgres.py    # → live Postgres table
```

See [08-domain-kg-pipeline.md](08-domain-kg-pipeline.md) for the full stage breakdown
(the old chunk/notebook/`populate_mcq_postgres.py` pipeline above this paragraph is
superseded — don't run it).

---

## 3. Model details

No custom-trained model in this repo — the ML components are all **inference-only calls
to pretrained/open-weight models**, orchestrated by deterministic pre/post-processing
code. There is no training loop, no model checkpointing beyond what's downloaded, and (per
§9) no experiment tracking actually wired up despite MLflow being deployed.

**Models in use:**
| Model | Role | Where |
| --- | --- | --- |
| Qwen2.5-VL-7B-Instruct | Captions extracted figures/visuals; can transcribe full page images | `src/captioning/qwen_vl.py::QwenVLCaptioner` |
| Qwen3.5-27B | Triple extraction, stem generation, self-critique/correction | `notebooks/mcq_generation.ipynb` |
| `all-MiniLM-L6-v2` (sentence-transformers) | Chunk embeddings for semantic splitting | `src/ingestion/semantic_chunk.py::SemanticChunker` |
| olmOCR-2-7B-1025 | PDF parsing experiment (structure-preserving OCR) | `notebooks/olmocr.ipynb` — **not wired into production path** |
| Infinity-Parser2-Pro | PDF parsing experiment (layout extraction) | `notebooks/infinity_parser.ipynb` — **not wired into production path** |

**Key config:**
- Triple extraction runs `enable_thinking=False` (structured extraction, not open-ended
  reasoning).
- Difficulty is a fixed 3-tier scale (1=recall, 2=application, 3=reasoning); relation
  taxonomy is a fixed 13-relation set (`PRODUCES`, `REGULATES`, `INHIBITS`, ... — full
  list in [04-mcq-generation.md](04-mcq-generation.md)).
- Self-critique correction is capped at **one** cycle (not iterated to convergence) — a
  deliberate simplification of the MCQG-SRefine approach it's based on.
- Block-classification confidence threshold defaults to `0.7`
  (`src/post_process/pipeline.py::process_mineru_json`).
- Semantic chunking uses an adaptive percentile cosine-distance threshold plus a hard
  `max_chars=6000` fallback (see [02-conventions.md](02-conventions.md) #5).

**Experiment tracking.** None currently functional — **this is the biggest gap in this
section**. `docker-compose.yml` runs a full MLflow server (Postgres-backed, MinIO
artifact store), `mlflow` is a listed dependency, but **no file under `src/`, `app/`, or
`scripts/` imports `mlflow` anywhere**. There are no run links to give you, because no
runs are being logged. See §9 — resolve this before assuming any experiment history
exists to consult.

**Evaluation metrics / baselines.** No formal offline eval harness found (no
accuracy/precision benchmark script, no held-out test set for question quality). The only
automated quality signal is the self-critique pass baked into generation itself (§2) and
`src/evaluation/mineru_quality.py` (extraction-only, reference-free heuristics — garbage
character ratio, repeated-char runs, empty-block ratio — not question-quality). **Why
these specific models were chosen over alternatives is not documented** — ask the
outgoing owner; likely context (unified-memory / local-hardware constraints, see §4) but
not stated explicitly anywhere in-repo.

**Known failure modes / edge cases:**
- ~62% of generated questions require a correction pass before being valid (§2) — the
  generation pipeline is not reliably one-shot-correct.
- An earlier dual-pipeline approach (MinerU visuals + separate full-page VLM transcription,
  `notebooks/dual_pipeline_parsing.ipynb`) was tried and abandoned because MinerU's native
  text/OCR was judged low quality on this document set — this is *why* the current
  post-process pipeline exists. Don't re-attempt that path; it's documented as history in
  [03-ingestion-pipeline.md](03-ingestion-pipeline.md), not as a live alternative.
- MPS (Apple Silicon) has known PyTorch issues worked around explicitly in
  `src/captioning/qwen_vl.py` (references upstream PyTorch bugs #95409/#96113) — anyone
  running captioning on Apple Silicon should read that file's comments before assuming
  clean GPU behavior.
- Stem-generation has a prompt-level (not code-enforced) instruction not to leak the
  answer into the question stem — this is **not validated in code**, so leakage is
  possible and wouldn't be caught automatically.

---

## 4. Code & environment

**Repo structure / entry points:**
```
app/                FastAPI app — HTTP surface (routers: students, quiz, flashcards)
src/
  post_process/      8-stage MinerU-output cleanup (deterministic, rule-based)
  captioning/        Qwen2.5-VL figure captioning
  ingestion/         unify + semantic chunking
  student_kg/        Neo4j student/session/streak/energy primitives
  quiz/               question bank loader + attempt recording
  flashcards/         card/review/session logic (Anki-style)
  domain_kg/           staged KG pipeline (stages 1-8): textbook markdown -> RDF graph
                      + UMLS links + MCQs (mcq.py). Source of the live biochem question
                      bank as of 07050b5/a0a7438 — see
                      [08-domain-kg-pipeline.md](08-domain-kg-pipeline.md).
  evaluation/          mineru_quality.py — diagnostic-only, not production path
scripts/
  ingest_data.py                    PDF(s) → chunks — the production ingestion CLI
                                     (superseded by src/domain_kg/ for the live bank)
  populate_biochem_mcq_postgres.py  domain_kg questions.json → live Postgres table
  populate_mcq_postgres.py          superseded anatomy-era loader — do not run
  generate_dummy_interactions.py    synthetic demo student data (seeded, see below)
notebooks/           interactive/manual pipeline stages, MCQ generation, parser spikes
tests/               pytest suite (see §7)
```
Full repo map with rationale: [01-architecture.md](01-architecture.md).

**Environment setup:**
- Two separate Python envs, both **required** for the app + main ingestion path: `.venv`
  (main, `uv`-managed, `pyproject.toml`, Python ≥3.13 — everything except MinerU) and
  `.venv-mineru` (isolated, MinerU only, created per MinerU's own install docs — **not**
  in `pyproject.toml` by design, since MinerU's deps conflict with the main env).
- A **third, so-far-uncommitted** env is now needed for `domain_kg` Stage 2/3
  (scispaCy + spaCy `<3.8` + faiss-cpu need Python 3.9–3.11, incompatible with the main
  `.venv`'s `>=3.13` pin) — see [08-domain-kg-pipeline.md](08-domain-kg-pipeline.md). No
  recipe for it is checked in yet.
- `cp .env.example .env` (or `make setup`, which every other `make` target depends on).
- Infra via Docker Compose (`docker-compose.yml`): Postgres, MinIO, MLflow, Neo4j. `make
  neo4j-up` is the minimum to run the API; `make mlflow-up` additionally brings up
  Postgres (needed for the `mcq_questions` table the API reads from — despite the name,
  MLflow itself isn't in the app's request path).
- **Hardware assumptions**: `src/captioning/qwen_vl.py::get_device()` picks CUDA → MPS →
  CPU in that order — code has explicit Apple Silicon (MPS) support and workarounds, so
  this has plausibly been developed/run on a Mac. No documented minimum VRAM/RAM, but
  running Qwen2.5-VL-7B and Qwen3.5-27B locally implies a non-trivial GPU or unified-memory
  budget — confirm actual hardware used with the outgoing owner if reproducing training/
  inference elsewhere.
- No app-level `Dockerfile` — only `docker/mlflow.Dockerfile` for the MLflow tracking
  server image. Running the FastAPI app itself is always local (`uvicorn`), not
  containerized.

**Reproducibility notes:**
- `scripts/generate_dummy_interactions.py` seeds `random.seed(42)` for reproducible
  synthetic demo-student data — the **only** explicit seed found in the repo.
- No seeding found for the LLM/VLM inference calls (triple extraction, captioning, stem
  generation) — outputs are **not guaranteed reproducible run-to-run**; this is inherent
  to the generation approach (temperature-based LLM sampling), not an oversight to fix
  casually.
- No seeding for `sentence-transformers` embeddings — embedding generation itself is
  deterministic given fixed model weights and input, so this is lower-risk.
- Exact rerun commands are the same as the "regenerating a dataset snapshot" commands in
  §2.

**Testing environment / commands:** see §7.

---

## 5. Infrastructure & deployment

**Where it runs.** No deployment target found in-repo — everything described in
`docker-compose.yml` is **local development infrastructure** (Postgres, MinIO, MLflow,
Neo4j), not a production topology. The FastAPI app itself has no containerization, no
cloud config, no edge/on-device packaging. **If a deployed/production environment exists,
it is not represented in this repo** — get that directly from the outgoing owner; don't
assume none exists just because it's undocumented here.

**CI/CD.** None. No `.github/workflows/`, no other CI config found anywhere in the repo.
Nothing runs tests, lint, or builds automatically on push or PR — confirmed still true as
of this handover (previously flagged in [07-operations.md](07-operations.md), still
accurate).

**Rollback procedure.** Not applicable / not documented — there's no deployment pipeline
to roll back. At the infra level, `make down` stops the local Docker stack without data
loss; `make clean` stops it **and destroys volumes** (Postgres + Neo4j + MinIO data) —
destructive, confirm before running against anything with data worth keeping.

**Monitoring / alerting.** None found. No APM integration, no drift detection, no
accuracy-decay tracking, no latency dashboards. `GET /health` exists as a bare liveness
endpoint (`app/main.py`) but nothing consumes it automatically (no configured
uptime/monitoring service found in-repo). Rate limiting exists (`slowapi`,
`app/limiter.py`) and centralized error responses exist (`app/errors.py`), but these are
request-handling correctness features, not observability — there's no metrics
export/logging pipeline wired up that this scan could find.

---

## 6. Decisions log (ADR-style, reconstructed from docs + commit history)

**Two decoupled halves, not one pipeline.** Offline content generation and live serving
share no runtime dependency — serving reads only a Postgres table populated by a
one-way, human-reviewed hand-off. *Alternative considered*: a single continuous
pipeline. *Why not*: would force the serving layer to depend on Torch/Transformers/GPU
availability, and would make "why is this question wrong" and "why did this request
fail" the same debugging surface instead of two separable ones. See
[01-architecture.md](01-architecture.md).

**Bearer session tokens, never JWT.** Opaque, random (`secrets.token_urlsafe(32)`),
server-revocable, only the SHA-256 hash persisted. *Alternative considered*: stateless
JWT. *Why not*: JWTs can't be revoked server-side without an additional denylist, and this
system needs actual revoke/expire control. See [06-student-graph.md](06-student-graph.md).

**`/quiz/.../check` and `/quiz/.../log` are two endpoints, not one.** *Alternative
tried and reverted*: a single combined endpoint requiring `confidence` up front. *Why
reverted*: the real frontend flow grades instantly on option-pick, then asks for
confidence after — the combined design forced two HTTP calls anyway, producing **two
separate `InteractionEvent` nodes per question** instead of one (confirmed by direct
Neo4j Browser inspection). An intermediate "write null confidence, then SET it later" fix
was also considered and rejected, because it left a briefly-incomplete event in an
event-sourced graph where every event is meant to be a complete, immutable fact. Current
split (`/check` fully stateless, `/log` a single atomic write) is the resolution. **Do
not re-merge these** unless the frontend flow itself changes. Full history:
[05-serving-api.md](05-serving-api.md).

**No asserted `Mastery` node — mastery is derived, not stored as fact (original intent),
vs. `:MASTERS` edge as actually built (verified).** Original design intent
(`src/student_kg/enrollment.py` module docstring): mastery should be measured from event
history, never asserted at a point in time. A `PUT /quiz/mastery` endpoint now exists and
**was traced directly in `src/quiz/mastery.py`** (not left as an open question): `p_know`
is computed **client-side** by the frontend's own BKT implementation, and the server does
zero recomputation/clamping/validation — by its own module docstring, "a thin,
deliberately dumb persistence layer." It overwrites the `:MASTERS` edge wholesale on
every `PUT`, with no history retained server-side, even though the event history it's
presumably derived from (`QUIZ_ANSWER`, `FLASHCARD_REVIEW`) is still fully intact and
queryable underneath it. So: not a clean continuation of the original principle (a value
*is* now being asserted and stored), but not a silent violation either (the server still
does no inference of its own — it's storing the client's derived number, not fabricating
one). Whether that distinction is an acceptable reading of the original design intent or
a walk-back of it is a **judgment call for whoever owns the BKT client**, not something
resolvable from code alone — raise it directly. Full trace: [06-student-graph.md](06-student-graph.md#mastery--masters-not-event-sourced-by-design-trade-off).

**Dual-pipeline parsing (MinerU visuals + separate VLM full-page transcription) tried
and abandoned.** *Why*: MinerU's own text/OCR was judged low-quality on this document
set, but the two-thread approach (joined only by page index, no shared item ID) was
replaced by the current post-process pipeline (rule-based cleanup + targeted visual
captioning). Kept in-repo as history (`notebooks/dual_pipeline_parsing.ipynb`), not as an
alternative to pick between. See [03-ingestion-pipeline.md](03-ingestion-pipeline.md).

**Two isolated Python environments (`.venv` / `.venv-mineru`).** *Why*: MinerU has
dependency constraints that conflict with the main project environment. *Alternative*:
one shared environment. *Why not*: would force dependency resolution across two
independently-evolving toolchains; kept separate, MinerU invoked only as a subprocess.

**Quiz and flashcard spaced repetition are two separate engines, not a shared one.**
Quiz answers update a `Student -[:REVIEWING]-> Question` edge (streak/interval driven by
correctness **and** confidence — only a confident-correct answer counts as a "strong
pass"); flashcard reviews update a dedicated `Flashcard` node instead (streak/interval
driven by a 4-point Again/Hard/Good/Easy self-rating, different growth multipliers per
rating). *Why not share one mechanism*: stated directly in
`src/flashcards/reviews.py`'s module docstring — "a card drilled to Easy and a question
answered confident-and-correct are different kinds of evidence, and neither should
silently update the other's schedule." Not documented before this handover; see
[06-student-graph.md](06-student-graph.md) for both mechanisms side by side.

---

## 7. Operations runbook

**Common failure modes:**
- API fails to start / 500s on first request → `make neo4j-up` (and `make mlflow-up` for
  Postgres) not running, or `.env` not populated. `app/dependencies.py` connects to Neo4j
  lazily on first request; `src/quiz/bank.py` connects to Postgres similarly.
- Quiz/flashcard endpoints return empty → `mcq_questions` table not populated. Run
  `scripts/populate_biochem_mcq_postgres.py` (needs Postgres reachable + `.env`
  `POSTGRES_*` set).
- Updated question content not reflected → `src/quiz/bank.py` caches the bank
  in-process (`@lru_cache(maxsize=1)`) — **restart the server** after re-populating.
- `scripts/ingest_data.py` fails at a MinerU subprocess step → `.venv-mineru` doesn't
  exist yet or isn't set up per MinerU's own install instructions (it's deliberately not
  part of `pyproject.toml`).
- CORS errors from a frontend → `CORS_ALLOWED_ORIGINS` env var not set (defaults to
  empty = no origins allowed). Not currently documented in `.env.example` — add the
  frontend's origin explicitly.
- 429 responses → rate limiting (`slowapi`) kicked in; check `app/limiter.py` for
  configured limits.

**How to retrain / regenerate.** There is no "training" step (§3) — the equivalent
operation is regenerating the question bank. See §2's "Regenerating a dataset snapshot."
No automated retraining trigger exists; it's a fully manual, reviewed process by design.

**How to roll back a bad deploy.** Not applicable — no deployment pipeline exists to roll
back (§5). At the data level: each chapter's `questions.json` under
`src/domain_kg/data/docs/` is git-tracked, so a bad regeneration can be reverted via git;
the live-serving `mcq_questions` Postgres table has no documented backup/versioning
beyond whatever `questions.json` state was last populated — re-running
`populate_biochem_mcq_postgres.py` against a previous git revision of those files is the
closest thing to a rollback procedure currently available.

---

## 8. Access & contacts

**Credentials / secrets.** `.env.example` only contains local dev-default placeholder
credentials (e.g. `mlflow`/`mlflow`, `minioadmin`/`minioadmin`,
`neo4j_password_change_me`) — no real secrets are checked into this repo. **If a
shared/production environment with real credentials exists, where those live (password
manager, cloud secrets manager, etc.) is not documented in-repo** — get that pointer
directly from the outgoing owner; do not assume it doesn't exist just because this scan
found nothing.

**Repo / dashboard links.** GitHub remote: `medical-uc/intelligent-tutoring-system-for-medical-student`
(from `git log` merge commit history). No other dashboards (no monitoring, no MLflow
runs to link — see §3/§5) were found wired up.

**Who to ask for what.** Not established from repo content alone — no `CONTRIBUTORS`
file or team roster in-repo. Git history shows a consistent commit pattern but doesn't
name a team. **Fill in directly with the outgoing owner**: who owns the GitHub org/repo
access, who built `src/flashcards/`, `src/student_kg/energy.py`,
`src/student_kg/streak.py`, and the parser notebooks (`olmocr.ipynb`,
`infinity_parser.ipynb`), and who to ask about the original problem statement / success
criteria referenced in §1.

---

## 9. Open items & roadmap

**Technical debt:**
- MLflow deployed (Postgres + MinIO + tracking server), never called anywhere in
  `src/`/`app/`/`scripts/`. Either wire it up to actually log the MCQ-generation
  pipeline's runs, or remove the infrastructure — confirm intent with whoever provisioned
  it before doing either.
- Zero CI — tests exist (`tests/`, runnable via `.venv/bin/pytest`) but nothing runs them
  automatically on push/PR.
- MCQ generation is notebook-only, not a script — promoting it to mirror
  `scripts/ingest_data.py`'s CLI structure is reasonable, optional future work.
- Stale prototype file `data/mcq/mcqs.json`, nothing reads it — candidate for deletion.
- `CORS_ALLOWED_ORIGINS` not documented in `.env.example` — silent footgun for fresh
  setup.
- `domain_kg` Stage 2/3 need a separate Python 3.11 env (scispaCy/spaCy/faiss-cpu
  conflict with the main `>=3.13` pin) with no committed setup recipe — next person to
  run those stages has to reconstruct it from source imports. See
  [08-domain-kg-pipeline.md](08-domain-kg-pipeline.md).
- **No database-enforced uniqueness on `Question.uid`, `Flashcard.uid`, or `Topic.path`.**
  `src/student_kg/driver.py::ensure_constraints` covers `Student`/`InteractionEvent`/
  `Session` but not these three — their dedup currently relies entirely on every write
  site consistently using `MERGE` instead of `CREATE` (true today in
  `attempts.py`/`reviews.py`/`mastery.py`, verified directly), not on a schema-level
  guarantee. One future `CREATE` typo would silently start duplicating nodes. Cheap to
  fix, not urgent, but worth adding before this gets forgotten.
- **Two independently-built `Topic` node graphs converge on the same nodes by `path`
  string, with no shared construction path.** `src/quiz/attempts.py::record_attempt`
  builds a `Topic` hierarchy (`SUB_TOPIC_OF` chain) from each answer's `topic_tag`;
  `src/quiz/mastery.py::upsert_mastery` separately `MERGE`s a `Topic` node purely as a
  `:MASTERS` edge target. They only stay consistent because both pass the same
  `topic_path` string convention — nothing enforces that at the schema level.

**Unfinished experiments:**
- `notebooks/olmocr.ipynb` (olmOCR-2-7B-1025) and `notebooks/infinity_parser.ipynb`
  (Infinity-Parser2-Pro) — both alternative PDF-parsing approaches, neither wired into
  the production `scripts/ingest_data.py` path. No documented decision on whether either
  is meant to replace or supplement MinerU. This is likely the actual in-flight work
  behind the `feat/textbook-parser` branch name — confirm scope/intent directly.
- ~~`src/domain_kg/` not called from anywhere / relation to
  `notebooks/mcq_generation.ipynb` undecided~~ — resolved as of `07050b5`/`a0a7438`:
  `src/domain_kg/` (via `mcq.py` + `populate_biochem_mcq_postgres.py`) is now the sole
  live pipeline; the old notebook/`question_bank.json`/`populate_mcq_postgres.py` chain
  is superseded (see §2, §4). Textbook corpus also swapped from anatomy/endocrine to
  biochem chapters in the same change.

**Needs a judgment call, not just documentation:**

- `PUT /quiz/mastery` — traced in full (§6 above, [06-student-graph.md](06-student-graph.md),
  and the full client call sequence in
  [10-frontend-integration.md § 4](10-frontend-integration.md#4-quiz-flow--setup--answer-loop--finish)):
  client computes `p_know`, server persists it verbatim with no audit trail. Not a code
  bug, but a real question of whether this satisfies the original "mastery is never
  asserted" design intent — needs a decision from whoever owns the BKT client, not a fix.
- `GET /students/me/nudge` — endpoint exists, implementation traced (counts due quiz/
  flashcard items, surfaces the single soonest-due one), but intended frontend behavior
  around it is undocumented anywhere found — confirm the intended UX, not just the code.

**Next steps someone should pick up:**
1. Decide the canonical PDF parser (MinerU vs. olmOCR vs. Infinity-Parser2-Pro) and
   either merge or shelve `feat/textbook-parser` accordingly.
2. Resolve the MLflow wire-up-or-remove question.
3. Get a decision (not just a trace) on whether `:MASTERS`'s client-asserted value is
   acceptable relative to the original mastery design intent.
4. Establish an actual deployment path (currently none exists in-repo) if this is moving
   toward production use.
5. Add CI to run the existing `pytest` suite automatically.
6. Add uniqueness constraints for `Question.uid`/`Flashcard.uid`/`Topic.path`.
7. Get and record: original problem statement/success criteria, textbook licensing
   status, and where production credentials (if any) live — none of these are derivable
   from the repo itself.
8. ~~Decide how `src/domain_kg/` relates to `notebooks/mcq_generation.ipynb`~~ — done,
   `src/domain_kg/` won (see §2, §4, §9 above). Remaining follow-up: `generate_dummy_interactions.py`
   auto-sources topics from Postgres so it needs no code change, but the synthetic Neo4j
   data it already wrote reflects whatever bank was live at generation time — re-run
   `make populate` (postgres → neo4j → students, in that order) after any question-bank
   swap so demo data doesn't reference retired topics.
