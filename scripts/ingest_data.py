"""Batch/single-PDF driver for the v1 pipeline (see notebooks/v1_pipeline.ipynb
for the interactive, cell-by-cell version of the same stages):

    mineru extraction -> postprocess -> extract visuals ->
    classify visual relevance -> caption semantic visuals ->
    unify text -> semantic chunk

Usage (run with the project's main .venv — .venv-mineru is MinerU-only):
    .venv/bin/python scripts/ingest_data.py --pdf path/to/file.pdf
    .venv/bin/python scripts/ingest_data.py --dir path/to/pdf_dir
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

MINERU_BIN = PROJECT_ROOT / ".venv-mineru" / "bin" / "mineru"
OUTPUT_ROOT = PROJECT_ROOT / "output"

from tqdm import tqdm

from captioning.qwen_vl import QwenVLCaptioner
from ingestion.middle_visuals import extract_visuals
from ingestion.semantic_chunk import SemanticChunker
from ingestion.unify import build_unified_items_from_postprocessed, render_unified_markdown, render_unified_text
from ingestion.upload_visuals import make_client as make_s3_client
from ingestion.upload_visuals import upload_captions_file
from post_process.pipeline import process_mineru_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("run_pipeline")

HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)


def _first_heading(text: str) -> str | None:
    match = HEADING_RE.search(text)
    return match.group(1).strip() if match else None


def run_mineru(pdf_path: Path) -> None:
    cmd = [str(MINERU_BIN), "-p", str(pdf_path), "-o", str(OUTPUT_ROOT), "-b", "pipeline", "-m", "auto"]
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        log.error(result.stdout[-4000:])
        log.error(result.stderr[-4000:])
        raise RuntimeError(f"MinerU extraction failed for {pdf_path}")


def run_pipeline(pdf_path: Path, captioner: QwenVLCaptioner, s3_client, bucket: str) -> Path:
    """Runs every stage for one PDF and writes all intermediate + final
    artifacts under output/{pdf_stem}/auto/. Returns the chunks JSON path."""
    pdf_stem = pdf_path.stem
    run_dir = OUTPUT_ROOT / pdf_stem / "auto"

    log.info("[%s] MinerU extraction", pdf_stem)
    run_mineru(pdf_path)

    middle_path = run_dir / f"{pdf_stem}_middle.json"
    with open(middle_path) as f:
        middle_document = json.load(f)

    log.info("[%s] Postprocess", pdf_stem)
    postprocessed = process_mineru_json(str(middle_path))
    with open(run_dir / f"{pdf_stem}_post_processed.json", "w") as f:
        json.dump(postprocessed, f, indent=2, ensure_ascii=False)

    log.info("[%s] Extract visuals", pdf_stem)
    visuals_by_page_idx = extract_visuals(middle_document)
    page_text_by_idx = {p["page_num"] - 1: p["text"] for p in postprocessed["pages"]}

    all_visuals = [(page_idx, v) for page_idx, visuals in visuals_by_page_idx.items() for v in visuals]
    log.info("[%s] Classify visual relevance (%d visuals)", pdf_stem, len(all_visuals))
    for page_idx, visual in tqdm(all_visuals, desc=f"[{pdf_stem}] classify", unit="img"):
        slide_text = page_text_by_idx.get(page_idx, "")
        img_path = run_dir / "images" / visual.img_path
        visual.relevance = captioner.classify_image_relevance(str(img_path), slide_text)

    semantic_visuals = [v for visuals in visuals_by_page_idx.values() for v in visuals if v.relevance == "semantic"]

    log.info("[%s] Caption %d semantic visuals", pdf_stem, len(semantic_visuals))
    caption_results = []
    for visual in tqdm(semantic_visuals, desc=f"[{pdf_stem}] caption", unit="img"):
        img_path = run_dir / "images" / visual.img_path
        caption = captioner.caption(str(img_path))
        caption_results.append({
            "item_id": visual.item_id,
            "image_path": str(img_path),
            "content_type": visual.type,
            "caption": caption,
        })

    captions_path = run_dir / f"{pdf_stem}_v1_captions.json"
    with open(captions_path, "w") as f:
        json.dump(caption_results, f, indent=2)

    log.info("[%s] Upload semantic visuals to MinIO", pdf_stem)
    upload_captions_file(s3_client, bucket, captions_path)

    log.info("[%s] Unify text", pdf_stem)
    captions = {r["item_id"]: r["caption"] for r in caption_results}
    unified_items = build_unified_items_from_postprocessed(postprocessed, visuals_by_page_idx, captions)
    unified_text = render_unified_text(unified_items)
    unified_markdown = render_unified_markdown(unified_items)

    with open(run_dir / f"{pdf_stem}_v1_unified_text.txt", "w") as f:
        f.write(unified_text)
    with open(run_dir / f"{pdf_stem}_v1_unified_text.md", "w") as f:
        f.write(unified_markdown)

    log.info("[%s] Semantic chunk", pdf_stem)
    chunker = SemanticChunker()
    chunks = chunker.chunk(unified_items, percentile=95.0, max_chars=6000)
    chunker.unload()

    doc_title = _first_heading(unified_items[0].text) or pdf_stem
    stage1_chunks = []
    stage1_figures = []
    char_cursor = 0

    for i, chunk in enumerate(chunks):
        chunk_id = f"c{i:03d}"
        chunk_text = render_unified_markdown(chunk)
        section_title = _first_heading(chunk_text)
        section_path = [doc_title, section_title] if section_title else [doc_title]

        char_start = char_cursor
        char_end = char_start + len(chunk_text)
        char_cursor = char_end

        stage1_chunks.append({
            "chunk_id": chunk_id,
            "section_path": section_path,
            "char_start": char_start,
            "char_end": char_end,
            "text": chunk_text,
        })

        for item in chunk:
            if item.content_type not in ("image", "chart", "table", "diagram"):
                continue
            caption = captions.get(item.item_id)
            if not caption:
                continue
            stage1_figures.append({
                "fig_id": item.item_id,
                "image_path": next((r["image_path"] for r in caption_results if r["item_id"] == item.item_id), None),
                "caption": caption,
                "referenced_from": chunk_id,
            })

    stage1_output = {
        "doc_id": pdf_stem,
        "source": {"source_id": pdf_stem, "title": doc_title},
        "chunks": stage1_chunks,
        "figures": stage1_figures,
    }

    chunks_path = run_dir / f"{pdf_stem}_v1_chunks.json"
    with open(chunks_path, "w") as f:
        json.dump(stage1_output, f, indent=2)

    log.info("[%s] Done: %d chunks, %d figures -> %s", pdf_stem, len(stage1_chunks), len(stage1_figures), chunks_path)
    return chunks_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pdf", type=Path, help="Path to a single PDF to process.")
    group.add_argument("--dir", type=Path, help="Directory of PDFs (non-recursive) to process.")
    parser.add_argument("--bucket", default=os.environ.get("SEMANTIC_IMAGES_BUCKET", "semantic-images"))
    args = parser.parse_args()

    if args.pdf:
        assert args.pdf.exists(), args.pdf
        pdf_paths = [args.pdf]
    else:
        assert args.dir.is_dir(), args.dir
        pdf_paths = sorted(args.dir.glob("*.pdf"))
        assert pdf_paths, f"No PDFs found in {args.dir}"

    log.info("Processing %d PDF(s)", len(pdf_paths))
    captioner = QwenVLCaptioner()
    s3_client = make_s3_client()
    results = []
    for pdf_path in tqdm(pdf_paths, desc="PDFs", unit="pdf"):
        try:
            results.append(run_pipeline(pdf_path, captioner, s3_client, args.bucket))
        except Exception:
            log.exception("[%s] FAILED", pdf_path.stem)
    captioner.unload()

    log.info("%d/%d PDFs processed successfully", len(results), len(pdf_paths))


if __name__ == "__main__":
    main()
