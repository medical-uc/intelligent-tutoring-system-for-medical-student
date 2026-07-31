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
    students.py          register/login/logout (bearer session tokens)
    quiz.py               topics, questions, check/log answer endpoints
  dependencies.py        neo4j driver singleton, auth dependency
  schemas.py              Pydantic request/response models

src/
  student_kg/             Neo4j student graph: driver, enrollment, sessions
  quiz/                   question bank loader + attempt recording (serving layer,
                          but content-aware, so it's separate from student_kg — see
                          06-student-graph.md's module map)
  ingestion/              PDF -> unified, chunked document (content pipeline)
  post_process/           8-stage rule-based MinerU-output cleanup (content pipeline)
  captioning/             vision-language captioning of extracted figures
  evaluation/             mineru_quality.py — diagnostic-only quality scorer, not
                          in the production data path

notebooks/                interactive pipeline stages + MCQ generation
  mcq_output/
    question_bank.json    GIT-TRACKED — the one generated artifact the live app depends on

scripts/
  ingest_data.py           production CLI driver for the content pipeline

data/                     GITIGNORED — source PDFs + a stale prototype MCQ file
output/                   GITIGNORED — per-PDF MinerU + pipeline intermediate artifacts

docker/, docker-compose.yml, Makefile   infra: neo4j, postgres/minio/mlflow
```

See [02-conventions.md](02-conventions.md) for why the gitignore split is deliberate.

## What's NOT wired together

**MLflow is deployed but unused.** `docker-compose.yml` runs a full MLflow tracking
server backed by Postgres and MinIO, and `mlflow` is a listed dependency in
`pyproject.toml` — but no file under `src/`, `app/`, or `scripts/` imports `mlflow`
anywhere. It's either a dead integration or infrastructure provisioned ahead of actual
instrumentation of the MCQ-generation pipeline; which one is currently unclear. See
[07-operations.md](07-operations.md) for the full known-gaps list.
