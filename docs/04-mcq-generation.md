# MCQ Generation

Scope: [`_v1_chunks.json`](03-ingestion-pipeline.md) in,
[`notebooks/mcq_output/question_bank.json`](../notebooks/mcq_output/question_bank.json)
out. This entire stage is currently notebook-driven
([`notebooks/mcq_generation.ipynb`](../notebooks/mcq_generation.ipynb), 50 cells) rather
than a script — promoting it to a repeatable CLI script (like
`scripts/ingest_data.py` does for the ingestion half) is optional future work, not a gap
that blocks anything today.

## The 6 steps

**Step 1 — load + filter chunks.** Loads every `_v1_chunks.json` under `output/`,
merges figure captions into their parent chunk (captions are already inlined as
`[FIGURE:fig_id] <caption>` text by the ingestion pipeline), filters out
reference/bibliography sections, sub-100-char stub chunks, and figure-only chunks. Builds
an enriched `context` string by prepending the most specific heading (last element of
`section_path`) so the LLM has the governing concept before reading fragment text.

**Step 2 — triple extraction.** One Qwen3.5-27B call per chunk extracts candidate
`{subject, relation, object}` triples plus a per-triple difficulty tier, constrained to a
fixed 13-relation taxonomy: `PRODUCES, REGULATES, INHIBITS, STIMULATES, ACTS_ON, PART_OF,
LOCATED_IN, INNERVATES, SUPPLIES, CAUSES, DIAGNOSED_BY, TREATED_BY, PREREQUISITE_OF`.
Difficulty is a 3-tier scale (1 = recall, 2 = application, 3 = reasoning). Output is
validated with Pydantic (`Triple`, `TripleExtractionResult` — see
[Conventions #1](02-conventions.md)). Runs with `enable_thinking=False` since this is
structured extraction, not open-ended reasoning. The extractor is loaded once and
released after use, following the same load/unload discipline as `QwenVLCaptioner` (see
[Conventions #3](02-conventions.md)) — same unified-memory budget constraint applies.

**Step 3 — distractor generation.** Two-tier: Tier 1 is corpus-level retrieval — for the
same `(topic, relation)` pair, find other objects seen elsewhere in the corpus, used as a
same-entity-type proxy since there's no real entity typing. Tier 2 is an LLM fallback
(reuses the already-loaded triple-extractor model, no second load) when Tier 1 doesn't
yield 3 candidates. Result is a `DistractorSet` Pydantic model with a validator that
rejects any distractor duplicating the correct answer.

**Step 4 — stem generation.** One LLM call per triple + distractor set. Instructions are
tier-specific: tier 3 (reasoning) explicitly asks for a short clinical vignette. The
prompt instructs the model not to leak the answer text/synonym into the stem — this is a
prompt-level constraint only, not separately validated in code.

**Step 5 — self-critique validation.** A `Critique` Pydantic model checks four things
against the source passage: stem unambiguous, correct answer definitively supported, no
distractor arguably also correct, difficulty tier matches the cognitive demand. If
invalid, a `Corrector` does one minimal-edit fix pass — capped at a single cycle, not
iterated to convergence (a deliberately simplified version of the MCQG-SRefine approach
it's based on). Items that still fail after correction are dropped, not shipped as
known-bad.

**Step 6 — serialization.** `serialize_mcq` shuffles the four options and flags which one
is `correct`, then groups everything by `doc_id` → `section_path` (joined with `" > "`)
into the final `question_bank.json`. The notebook's own comment notes the joined section
path is meant to align with a future Neo4j community structure — it doesn't exist yet,
this is forward-looking naming, not a currently-active graph feature.

## Output shape

```text
{doc_id: {topic_path: [question, ...]}}
```

Each question object:

```json
{
  "uid": "doc::chunk::t0000",
  "stem": "...",
  "options": [{"text": "...", "correct": true}, ...],
  "source": {"doc_id": "...", "chunk_id": "..."},
  "triple": {"subject": "...", "relation": "...", "object": "..."},
  "difficulty": 1,
  "topic_tag": ["SECTION", "SUBSECTION"],
  "distractor_sources": ["same_section", "llm", "..."],
  "critique": {"valid_as_generated": true, "reason": null}
}
```

**`critique.valid_as_generated` is `not was_corrected`** — it's `false` for any question
that needed a Step 5 correction pass, not only for questions that were uncorrectable and
dropped. In the current data, 116 of 188 total questions (~62%) have
`valid_as_generated: false`. `src/quiz/bank.py`'s `_is_usable()` filter treats any
`false` value as unusable and excludes it from what's served to students — meaning the
serving layer is currently more conservative than it strictly needs to be (it discards
corrected-and-now-valid questions along with genuinely dropped ones). If that 62%
exclusion rate becomes a problem, revisit whether corrected-but-valid items should be
distinguished from uncorrectable ones before filtering — that distinction isn't currently
captured in the schema (`was_corrected` exists in the notebook's `ValidatedMCQItem` but
isn't serialized separately from `valid_as_generated`).

## Why it's committed

`question_bank.json` is the one generated artifact treated as a committed deliverable,
unlike everything under `output/`/`data/` (both gitignored). This is because
`src/quiz/bank.py`'s default `QUESTION_BANK_PATH` points directly at it — the live
FastAPI app depends on this file existing in the repo, not on it being regenerated at
deploy time. See [Conventions #11](02-conventions.md).

## Stale artifact

[`data/mcq/mcqs.json`](../data/mcq/mcqs.json) is an older prototype MCQ format (`uid,
doc_name, window_index, stem, correct_answer, distractors` — no `topic_path`, no
`options[].correct`, no critique/provenance) that predates the current schema. Nothing in
`src/` or `app/` reads it. It's a candidate for deletion, flagged here rather than
removed, per [Conventions #10](02-conventions.md) (superseded approaches get flagged, not
silently deleted, in case something still references them for historical reasons).
