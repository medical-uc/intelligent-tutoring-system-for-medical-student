"""domain_kg — staged medical knowledge-graph construction pipeline (Stages 1-8).

Ported from the standalone `knowledge-graph-pipeline` (medkg) repo.

Importing this package pins native thread counts (see `native_env`) BEFORE
torch, spaCy/thinc or faiss can load. That ordering is the whole point: those
libraries each build a threading pool at import time, and on macOS two of them
doing so in one process segfaults with no Python traceback. Setting the counts
afterwards is too late.

Layout:
    stages/   Stage 1-8 pipeline modules (stage1_parse .. stage8_serve)
    llm/      Injectable LLM backends (Claude via stage2, meditron, huatuo)
    cli/      Entrypoints: run, subset, build_index, build_ontology, query
    data/     Bundled ontology JSON + generated graph artifacts + source docs
    (root)    Core: ir, config, ontology, console, guards, corpus, mcq
"""
from . import native_env as native_env  # noqa: F401  -- must be first

__all__ = [
    "ir", "config", "console", "native_env", "ontology", "guards", "corpus", "mcq",
]
