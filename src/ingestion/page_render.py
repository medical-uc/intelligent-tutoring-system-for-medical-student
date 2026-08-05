"""Renders PDF pages to images via PyMuPDF, for feeding whole pages to a
VLM for text transcription (Thread B of the dual-pipeline architecture —
see `notebooks/dual_pipeline.ipynb`)."""

from pathlib import Path

import fitz  # PyMuPDF

DEFAULT_DPI = 150


def render_pages(
    pdf_path: Path, output_dir: Path, dpi: int = DEFAULT_DPI
) -> list[Path]:
    """Renders every page of `pdf_path` to a PNG in `output_dir`, named
    `page_{page_idx:04d}.png` (page_idx is 0-indexed, matching MinerU's
    `page_idx` field). Returns the list of image paths in page order."""
    output_dir.mkdir(parents=True, exist_ok=True)
    zoom = dpi / 72  # PDF points are 72 per inch

    paths = []
    with fitz.open(pdf_path) as doc:
        for page_idx, page in enumerate(doc):
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            image_path = output_dir / f"page_{page_idx:04d}.png"
            pix.save(image_path)
            paths.append(image_path)

    return paths
