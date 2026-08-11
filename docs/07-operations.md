# Operations

How to run everything, and what's currently unfinished.

## Local setup

Two Python environments:
- **`.venv`** — the main project environment (`uv`-managed, `pyproject.toml`,
  `requires-python >= 3.13`). Everything except MinerU runs here: the FastAPI app, the
  ingestion pipeline's Python code, the notebooks.
- **`.venv-mineru`** — an isolated environment for MinerU alone, invoked only as a
  subprocess from `scripts/ingest_data.py`. Kept separate because MinerU has dependency
  constraints that conflict with the main project's environment. If this doesn't exist
  yet, `scripts/ingest_data.py` will fail at the MinerU subprocess step — the fix is
  creating it per whatever MinerU's own install instructions require (not part of
  `pyproject.toml`'s dependency list by design, see [Conventions](02-conventions.md)).

Environment variables: copy `.env.example` to `.env` (the `make setup` target does this
automatically, and every other `make` target depends on `setup` first). Notable vars:
`NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` (read by
`src/student_kg/driver.py::make_driver`), `POSTGRES_USER`/`POSTGRES_PASSWORD`/
`POSTGRES_DB`/`POSTGRES_HOST`/`POSTGRES_PORT` (read by `src/quiz/bank.py` to serve quiz
questions from the `mcq_questions` table — same creds mlflow's backend store uses),
`SEMANTIC_IMAGES_BUCKET` (read by `scripts/ingest_data.py`, defaults to
`semantic-images`).

**Not currently in `.env.example`, but read by the app:** `CORS_ALLOWED_ORIGINS`
(comma-separated list, read by `app/main.py`, defaults to empty — no origins allowed —
if unset). Anyone doing a fresh setup with a frontend will hit CORS errors with no clue
why unless they know to set this; add it to `.env.example` when convenient.

## Running the stack

`Makefile` wraps `docker compose -f docker-compose.yml --env-file .env`:

| Target | Starts |
| --- | --- |
| `make neo4j-up` | Just `neo4j` — the minimum needed to run the FastAPI app. |
| `make mlflow-up` | `postgres` + `minio` + `minio-init` + `mlflow` — the experiment-tracking stack (see "Known gaps" below re: whether anything actually uses this yet). |
| `make minio-up` | Just `minio` + `minio-init` (bucket bootstrap) — needed if running the ingestion pipeline's visual upload step (`src/ingestion/upload_visuals.py`) standalone. |
| `make up` | Everything. |
| `make down` / `make clean` | Stop the stack / stop **and destroy volumes** (`clean` is destructive — confirm before running against anything with data you care about). |
| `make psql db=<name>` | Shell into the postgres container (defaults to `db=mlflow`). |
| `make server` | Runs the API itself — `uvicorn app.main:app --reload --reload-dir app --host 0.0.0.0 --port 8000` — as an alternative to the manual `uvicorn` command below. |
| `make populate-postgres` | Brings up postgres, then runs `scripts/populate_mcq_postgres.py` — see "Populating quiz questions" below. |
| `make populate-neo4j` | Brings up neo4j, then loads `src/domain_kg/graph.nq` via `src.domain_kg.load_neo4j` (the medical ontology graph, separate from the student graph — see [06-student-graph.md](06-student-graph.md)). |
| `make populate-students` | Brings up neo4j, then runs `scripts/generate_dummy_interactions.py` — seeds synthetic demo students/activity (seeded `random.seed(42)`, reproducible). |
| `make populate` | All three `populate-*` targets in sequence. |

## Running the API

```bash
.venv/bin/uvicorn app.main:app --reload
# or: make server
```

Requires `make neo4j-up` and `make mlflow-up` (or `make up`) running first —
`app/dependencies.py` connects to Neo4j on first request and `src/quiz/bank.py` connects
to postgres, both failing if unreachable. Postgres also needs `mcq_questions` populated —
see "Populating quiz questions" below. `CORS_ALLOWED_ORIGINS` also needs to be set in
`.env` if a frontend will call the API from a browser (see "Local setup" above).

## Running the ingestion pipeline

```bash
.venv/bin/python scripts/ingest_data.py --pdf path/to/file.pdf
.venv/bin/python scripts/ingest_data.py --dir path/to/pdf_dir
```

See [03-ingestion-pipeline.md](03-ingestion-pipeline.md) for what this actually does.
Output lands under `output/{pdf_stem}.pdf_origin/auto/`.

## Regenerating the question bank

Run [`notebooks/mcq_generation.ipynb`](../notebooks/mcq_generation.ipynb) end to end.
This is a **manual step, not a make target or script** — there is currently no automated
way to trigger MCQ regeneration from the CLI. See
[04-mcq-generation.md](04-mcq-generation.md) for what it does; output overwrites
`notebooks/mcq_output/question_bank.json`, which is git-tracked (commit the update
explicitly once you've reviewed it). This file is not read by the live app (see below) —
it's the generation pipeline's committed output artifact.

## Populating quiz questions

The live app serves quiz questions from the `mcq_questions` postgres table, not from
`question_bank.json` directly. Load it with:

```bash
.venv/bin/python scripts/populate_mcq_postgres.py
```

This reads `notebooks/master_mcq_with_topics.json` and upserts into `mcq_questions`
(creating the table if needed). Needs `make mlflow-up` (or `make up`) running first so
postgres is reachable, and `.env` populated with `POSTGRES_*` vars. Re-run after updating
the source JSON — `src/quiz/bank.py` caches the bank in-process
(`@lru_cache(maxsize=1)`), so a running server also needs a restart to pick up changes.
This same cached bank backs both the quiz router and the flashcards router
(`src/flashcards/cards.py`) — there's no separate flashcard content table to populate.

## Running tests

```bash
.venv/bin/pytest
```

`pyproject.toml` sets `testpaths = ["tests"]`, `pythonpath = ["src", "."]`. Current
coverage: `tests/conftest.py`, `test_students_api.py`, `test_quiz_api.py`,
`test_flashcards_api.py`, `test_error_handling.py`, `test_diagram_processing.py`. No
coverage tooling configured (no `pytest-cov`), and nothing runs this automatically — see
"Known gaps" below re: CI.

## Known gaps

Flagged here so nobody mistakes silence for "this is fine" — none of these block current
functionality, but a new engineer should know about them before assuming CI coverage
exists or that every deployed service is load-bearing.

- **Tests exist, but nothing runs them automatically.** `tests/` has real coverage (see
  "Running tests" above) — this is a change from an earlier version of this doc, which
  claimed zero tests existed. What's still true: no CI wiring runs `pytest` on push or
  PR, so a broken test doesn't block a merge.
- **Zero CI.** No `.github/workflows/`. Nothing runs automatically on push or PR.
- **MLflow: deployed, never called.** `docker-compose.yml` runs a full MLflow tracking
  server (postgres-backed, MinIO artifact store), and `mlflow` is a listed dependency in
  `pyproject.toml`, but no file under `src/`, `app/`, or `scripts/` imports `mlflow`
  anywhere. Two equally plausible explanations, and it's currently unclear which is
  true: (a) it's a dead integration nobody's cleaned up, or (b) it's infrastructure
  provisioned ahead of actually instrumenting the MCQ-generation pipeline's experiments.
  Worth confirming with whoever set up the docker-compose stack before either wiring it
  up or ripping it out.
- **Orphaned `run_pipeline.pyc`.** `src/ingestion/__pycache__/run_pipeline.cpython-313.pyc`
  exists with no corresponding `.py` source — git history shows it was moved and became
  `scripts/ingest_data.py` (commit `2959846`). Harmless (just a stale bytecode cache
  file, regenerated/ignored normally), but don't go looking for a
  `src/ingestion/run_pipeline.py` that doesn't exist — `scripts/ingest_data.py` is the
  real entry point.
- **Stale prototype data file.** `data/mcq/mcqs.json` — an old MCQ format nothing reads
  anymore, superseded by `question_bank.json`. See
  [04-mcq-generation.md](04-mcq-generation.md).
- **MCQ generation is notebook-only.** Not automated, not a script, not idempotent in
  any tracked way — see "Regenerating the question bank" above. Promoting it to a script
  mirroring `scripts/ingest_data.py`'s structure is reasonable future work, not a defect.
