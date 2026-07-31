# Conventions

This is a reference page. Later docs link here (`see Conventions #N`) instead of
re-explaining these patterns inline. Read it once before touching any module.

## Data modeling

**#1 — Dataclasses for pipeline data, Pydantic for LLM output.** Every pipeline-stage
data shape (`UnifiedItem` in [`src/ingestion/unify.py`](../src/ingestion/unify.py),
`Block`/`Feature`/`Node` in `src/post_process/models/`, `PageMetrics`/`DocMetrics` in
[`src/evaluation/mineru_quality.py`](../src/evaluation/mineru_quality.py)) is a plain
`@dataclass` — no validation needed because the producer controls the shape. Anything
that comes back from an LLM call instead gets a Pydantic `BaseModel` with
`field_validator`s enforcing domain invariants (e.g. `mcq_generation.ipynb`'s
`DistractorSet` rejects a distractor that duplicates the correct answer). Use this rule
to decide which to reach for when adding a new module: internal pipeline data →
dataclass, LLM response → Pydantic.

**#2 — Stable, reconstructable IDs carry provenance across stages.** Every stage's
output is joinable back to an earlier stage's output by string key alone, with no shared
in-memory state required: `{pdf_stem}#p{page_idx}#{index}` for content-list items,
`{doc_id}::{chunk_id}` for corpus chunks, `{chunk.uid}::t{i:04d}` for MCQ items (see
[`notebooks/mcq_generation.ipynb`](../notebooks/mcq_generation.ipynb) step 6). This is
why several notebook cells rebuild an object from disk rather than relying on kernel
state — the ID scheme makes that safe and cheap.

## Resource lifecycle

**#3 — Explicit `load()`/`unload()` on every model-wrapping class.** Lazy-load on first
use, explicit `gc.collect()` + `torch.mps.empty_cache()` on unload. See
[`src/captioning/qwen_vl.py:78-97`](../src/captioning/qwen_vl.py) (`QwenVLCaptioner.load`/
`.unload`) and [`src/ingestion/semantic_chunk.py:42-51`](../src/ingestion/semantic_chunk.py)
(`SemanticChunker.load`/`.unload`). This is a hard constraint, not style: the pipeline
runs on 24GB unified memory (Apple Silicon MPS) with only one large model resident at a
time. Batch drivers load once for the whole run and unload at the very end (see
[`scripts/ingest_data.py`](../scripts/ingest_data.py)), never per-item.

**#4 — MPS workarounds are documented inline with upstream issue numbers.**
[`src/captioning/qwen_vl.py:5-10`](../src/captioning/qwen_vl.py) explains, citing
`pytorch/pytorch#95409`/`#96113` and `huggingface/transformers#36413`/`#41908`, why the
model is always loaded in `bfloat16` (never `float16`) and always loaded to CPU before
`.to(device)` — both sidestep known unresolved MPS SIGSEGVs. If you hit a similar
MPS-specific crash elsewhere, check here first before re-deriving the same workaround.

## Design philosophy

**#5 — Adaptive thresholds over fixed magic numbers.**
[`src/ingestion/semantic_chunk.py:58,77`](../src/ingestion/semantic_chunk.py) splits at
the 95th-percentile cosine-distance jump *within that document* rather than a fixed
distance constant, so the threshold adapts to each document's own variance.
[`src/post_process/layout/header_footer.py:17-18`](../src/post_process/layout/header_footer.py)
does the same for repetition count: `min_repeats = 2 if pages_present <= 3 else 5`. When
adding a new heuristic, prefer a threshold derived from the input over a constant picked
by eye.

**#10 — Superseded approaches are kept, not deleted, and clearly marked.** The dual
visual+transcription pipeline
([`notebooks/dual_pipeline_parsing.ipynb`](../notebooks/dual_pipeline_parsing.ipynb)) was
tried and abandoned in favor of the current v1 pipeline, but left in the repo rather than
removed — same with the earlier LLaVA-Med + router captioning setup (removed in git
history, commit `3a6b3ce`, in favor of Qwen2.5-VL alone) and the old
[`data/mcq/mcqs.json`](../data/mcq/mcqs.json) prototype format superseded by
`question_bank.json`. See [03-ingestion-pipeline.md](03-ingestion-pipeline.md) for which
paths are live vs. historical. Don't delete a superseded approach without checking
whether it's referenced as "here's what we tried and why it didn't work" — but also don't
build new work on top of one without checking this page first.

## Code hygiene

**#6 — One logging convention everywhere.**
`logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")`
plus a per-module named logger, e.g. `logging.getLogger("run_pipeline")` in
[`scripts/ingest_data.py:40-41`](../scripts/ingest_data.py), `logging.getLogger("upload_visuals")`
in [`src/ingestion/upload_visuals.py:25-26`](../src/ingestion/upload_visuals.py),
`logging.getLogger("quiz.bank")` in [`src/quiz/bank.py:23`](../src/quiz/bank.py). Follow
this exact pattern for any new module that logs.

**#7 — Fail-soft per-item, fail-loud on setup.** Batch drivers catch and log exceptions
per-item so one bad input doesn't kill a multi-item run (`scripts/ingest_data.py`'s
per-PDF `try/except Exception` inside the batch loop), but structural preconditions
(missing venv, malformed root JSON, no matching student) `assert`/raise immediately with
an actionable message — e.g. `src/student_kg/session.py`'s
`assert record, f"session creation failed — no student with id={student_id}"`.

**#8 — CLI scripts reuse their module docstring as the `--help` text.** `argparse` is
built with `formatter_class=argparse.RawDescriptionHelpFormatter` and
`description=__doc__`, so the docstring at the top of the file and the actual `--help`
output can never drift apart. See [`scripts/ingest_data.py`](../scripts/ingest_data.py)
and [`src/student_kg/enrollment.py`](../src/student_kg/enrollment.py).

**#9 — Docstrings explain *why*, not just *what*.** This is pervasive enough to be house
style — e.g. why MPS float16 is avoided (#4 above), why a percentile threshold beats a
fixed one (#5 above), why session tokens are opaque and hashed rather than JWTs (see
[06-student-graph.md](06-student-graph.md)). When adding a docstring, prefer explaining
the non-obvious reason over restating the function name in prose.

## Repo hygiene

**#11 — The gitignore split is deliberate, not an oversight.** `output/` and `data/` are
both listed under `# Project-specific` in `.gitignore` — they're regenerate-on-demand
scratch space. But [`notebooks/mcq_output/question_bank.json`](../notebooks/mcq_output/question_bank.json)
and two smoke-test sample PDFs directly under `notebooks/` are committed on purpose,
because `question_bank.json` is the one generated artifact the live FastAPI app actually
depends on at runtime (`src/quiz/bank.py`'s `QUESTION_BANK_PATH` default). Don't "clean
up" that file, and don't assume everything under `notebooks/` follows the same ignore
rule as `output/`/`data/` — check `.gitignore` before deleting anything that looks like
generated output.
