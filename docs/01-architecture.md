# Architecture

## Why two halves

This system is two loosely-coupled halves that talk to each other through a file on
disk, not a shared service:

1. **Offline content pipeline** — turns raw PDF lecture slides into a bank of
   multiple-choice questions. Runs by hand (scripts + notebooks), on demand, whenever
   new source material shows up. Output: [`notebooks/mcq_output/question_bank.json`](../notebooks/mcq_output/question_bank.json).
2. **Live serving layer** — a FastAPI app that serves questions from that JSON file to
   students and records their answers in a Neo4j graph. Runs continuously as a server.

They're decoupled deliberately: the serving layer has zero dependency on the ML
pipeline's code, models, or GPU/MPS requirements — it just reads a JSON file at startup.
Regenerating the question bank never requires touching or redeploying the API, and the
API never needs Torch, Transformers, or a vision-language model loaded. If you're
debugging "why is this question wrong," you're in half 1. If you're debugging "why did
this HTTP request fail," you're in half 2.

**A third, standalone piece as of 2026-08-12:** `src/domain_kg/` — a staged pipeline
(textbook markdown → NER/relations → UMLS-linked RDF graph → MCQs) ported in from a
separate `medkg` repo. It doesn't talk to either half above yet — no import of
`domain_kg` exists outside itself. Treat it as a third, currently-disconnected system
until an integration decision is made. Full detail:
[08-domain-kg-pipeline.md](08-domain-kg-pipeline.md).

**A fourth piece:** Bayesian Knowledge Tracing, split deliberately across client and
server. `p_know` — the per-student, per-topic mastery estimate that drives
personalization ("what is this student weak in") — is computed **client-side only**
and pushed via `PUT /quiz/mastery` (see "Mastery" in
[06-student-graph.md](06-student-graph.md#mastery--masters-not-event-sourced-by-design-trade-off)).
The shared BKT *parameters* that computation runs on (`p_init`/`p_transit`/`p_slip`/
`p_guess`) are EM-fit server-side from every student's pooled interaction history
(`src/quiz/bkt_fit.py`, developed in `notebooks/knowledge_tracing.ipynb`) and served via
`GET /quiz/mastery/params` for the client to fetch/cache — a population-level fit, not
a per-student computation, so this doesn't move `p_know` itself server-side.
Full detail: [09-knowledge-tracing.md](09-knowledge-tracing.md).

## System diagram

```mermaid
flowchart LR
    PDF[PDF lecture slides] --> MinerU[MinerU extraction]
    MinerU --> PP["post_process (8 stages)"]
    PP --> Cap[Qwen2.5-VL captioning]
    Cap --> Unify[unify.py]
    Unify --> Chunk[semantic_chunk.py]
    Chunk --> Chunks[("_v1_chunks.json")]
    Chunks -.manual: run notebook.-> MCQGen["mcq_generation.ipynb\n(6 steps)"]
    MCQGen --> Bank[("question_bank.json")]
    Bank -.served by src/quiz/bank.py.-> API[FastAPI app]
    API <--> Neo4j[(Neo4j student graph)]
    Student([Student]) <--> API
```

The two dashed edges are the important signal: this is **not** one continuous automated
pipeline. There are two human-in-the-loop / file-handoff boundaries — running
`mcq_generation.ipynb` is a manual step, and the served half only ever reads the
resulting JSON, it doesn't trigger regeneration. See
[03-ingestion-pipeline.md](03-ingestion-pipeline.md) and
[04-mcq-generation.md](04-mcq-generation.md) for the left side of this diagram,
[05-serving-api.md](05-serving-api.md) and [06-student-graph.md](06-student-graph.md) for
the right side.

## Half 1 — content pipeline (summary)

PDF in, `question_bank.json` out. MinerU (an external tool, run from its own isolated
venv) extracts raw layout/text/images from each PDF; `src/post_process/` cleans that raw
output into a structured document tree; `src/captioning/` captions extracted
figures/visuals with a vision-language model; `src/ingestion/unify.py` joins text +
captions into one document; `src/ingestion/semantic_chunk.py` splits it into
retrieval-sized chunks. `scripts/ingest_data.py` drives all of that end-to-end per PDF.
A separate, notebook-only stage (`mcq_generation.ipynb`) then turns those chunks into
graded multiple-choice questions via LLM triple extraction, distractor generation, and
self-critique validation. Full detail: [03-ingestion-pipeline.md](03-ingestion-pipeline.md),
[04-mcq-generation.md](04-mcq-generation.md).

## Half 2 — serving layer (summary)

`question_bank.json` + student actions in, Neo4j graph state out. `src/quiz/bank.py`
loads and sanitizes the question bank once at startup (strips the answer key before any
question reaches a client, filters out questions flagged invalid by the pipeline's own
self-critique step). `app/routers/quiz.py` exposes topic/question listing plus a
two-step check-then-log answer flow. `src/student_kg/` and `src/quiz/attempts.py` model
students, sessions, and answer attempts as an append-only event chain in Neo4j. Full
detail: [05-serving-api.md](05-serving-api.md), [06-student-graph.md](06-student-graph.md).

## Repo map

```text
app/                    FastAPI app — the live serving layer's HTTP surface
  routers/
    students.py          register/login/logout/me + profile, streak, energy, nudge
    quiz.py               topics/subjects, questions, sessions, history, mastery,
                          check/log answer endpoints
    flashcards.py         Anki-style card catalog, sessions, reveal/log, history,
                          due-for-review — parallel structure to quiz.py
  auth_middleware.py     SessionAuthMiddleware — opens unauthenticated routes via
                          exact-path + regex allowlist (see module docstring for why
                          regex, not prefix matching)
  errors.py               centralized exception handlers, one error envelope
                          ({"error": {"code", "message", "details"}}) for every
                          HTTPException/validation/Neo4j/unhandled error
  limiter.py               slowapi rate limiter instance shared across routers
  openapi.py               install_openapi() + error_responses() helper used in
                          route decorators to document error envelopes per-endpoint
  dependencies.py         neo4j driver singleton, auth dependency
  schemas.py              Pydantic request/response models

src/
  student_kg/             Neo4j student graph: driver, enrollment, sessions,
                          streak.py (consecutive-day tracking + restore), energy.py
                          (gamification currency balance)
  quiz/                   question bank loader, attempt recording, sessions.py
                          (quiz-run start/end/cancel/history), mastery.py (stores
                          client-computed BKT p_know per topic — see below),
                          bkt_fit.py (EM-fits shared BKT params from pooled
                          interaction history, served via GET /quiz/mastery/params)
  flashcards/              cards.py (bank-derived flashcard view), reviews.py
                          (rating log + spaced-repetition due list), sessions.py
                          (batch start/end/cancel/history) — same event-sourced
                          pattern as quiz/, kept separate since flashcard scheduling
                          (streak/interval per card) differs from quiz's model
  ingestion/              PDF -> unified, chunked document (content pipeline)
  post_process/           8-stage rule-based MinerU-output cleanup (content pipeline)
  captioning/             vision-language captioning of extracted figures
  evaluation/             mineru_quality.py — diagnostic-only quality scorer, not
                          in the production data path
  domain_kg/              STANDALONE, not called from app/ or scripts/ — staged
                          KG pipeline (stages 1-8): textbook markdown -> UMLS-linked
                          RDF graph -> MCQs. stages/, cli/, llm/ (Claude/meditron/
                          huatuo backends), data/ (ontology + graph.nt/.nq + doc
                          fixtures, git-tracked). See
                          [08-domain-kg-pipeline.md](08-domain-kg-pipeline.md).

notebooks/                interactive pipeline stages + MCQ generation
  mcq_output/
    question_bank.json    GIT-TRACKED — the one generated artifact the live app depends on
  olmocr.ipynb             EXPERIMENTAL — olmOCR-2-7B-1025 PDF parsing spike, not
                          wired into scripts/ingest_data.py yet
  infinity_parser.ipynb    EXPERIMENTAL — Infinity-Parser2-Pro PDF parsing spike,
                          not wired into scripts/ingest_data.py yet

scripts/
  ingest_data.py           production CLI driver for the content pipeline
  populate_mcq_postgres.py  loads question content into the mcq_questions table

data/                     GITIGNORED — source PDFs + a stale prototype MCQ file
output/                   GITIGNORED — per-PDF MinerU + pipeline intermediate artifacts

docker/, docker-compose.yml, Makefile   infra: neo4j, postgres/minio/mlflow
```

See [02-conventions.md](02-conventions.md) for why the gitignore split is deliberate.

**Note on `olmocr.ipynb`/`infinity_parser.ipynb`:** alternative PDF-parsing approaches
added on the `feat/textbook-parser` branch, not yet integrated into the production
ingestion path (MinerU is still what `scripts/ingest_data.py` calls). No decision is
recorded yet on whether either replaces or supplements MinerU — see
[HANDOVER.md](HANDOVER.md) for the open item.

## What's NOT wired together

**MLflow is deployed but unused.** `docker-compose.yml` runs a full MLflow tracking
server backed by Postgres and MinIO, and `mlflow` is a listed dependency in
`pyproject.toml` — but no file under `src/`, `app/`, or `scripts/` imports `mlflow`
anywhere. It's either a dead integration or infrastructure provisioned ahead of actual
instrumentation of the MCQ-generation pipeline; which one is currently unclear. See
[07-operations.md](07-operations.md) for the full known-gaps list.
