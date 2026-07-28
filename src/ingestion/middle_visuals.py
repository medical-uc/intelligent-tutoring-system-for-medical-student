"""Extracts visual (image/chart/table-as-image) crops from a MinerU
`_middle.json`, for the v1 pipeline where `post_process.flatten_layout`
intentionally drops image blocks (text-only). Visuals are pulled here
independently, in parallel with `post_process`, and joined back by
page_idx.

A `table` block only lands here if MinerU couldn't OCR it to HTML (span
carries `image_path` instead of `html`) — HTML tables are already
structured text and go through `post_process` normally.
"""

from dataclasses import dataclass

VISUAL_TYPES = {"image", "chart", "table"}


@dataclass
class MiddleVisual:
    item_id: str
    page_idx: int
    type: str
    bbox: list[float]
    img_path: str
    relevance: str | None = None  # "semantic" | "decorative" | None (unclassified)


def extract_visuals(middle_document: dict) -> dict[int, list[MiddleVisual]]:
    """middle_document: parsed `_middle.json` (has top-level "pdf_info").
    Returns page_idx -> list[MiddleVisual], in block order."""
    pages: dict[int, list[MiddleVisual]] = {}

    for page in middle_document.get("pdf_info", []):
        page_idx = page.get("page_idx", 0)
        raw_blocks = page.get("para_blocks") or page.get("preproc_blocks", [])

        block_counter = 0
        for top_block in raw_blocks:
            if not isinstance(top_block, dict):
                continue
            block_counter += 1

            if (top_block.get("type") or "").lower() not in VISUAL_TYPES:
                continue

            img_path = None
            for sub in top_block.get("blocks", []) or []:
                for line in sub.get("lines", []):
                    for span in line.get("spans", []):
                        if span.get("image_path"):
                            img_path = span["image_path"]

            if not img_path:
                continue  # e.g. OCR'd table with html instead of a crop

            item_id = f"p{page_idx}_b{block_counter}"
            pages.setdefault(page_idx, []).append(
                MiddleVisual(
                    item_id=item_id,
                    page_idx=page_idx,
                    type=top_block["type"].lower(),
                    bbox=top_block.get("bbox", [0, 0, 0, 0]),
                    img_path=img_path,
                )
            )

    return pages
