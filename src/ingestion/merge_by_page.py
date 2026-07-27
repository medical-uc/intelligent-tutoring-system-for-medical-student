"""Merges the dual-pipeline architecture's two independent streams into
one page-grouped document:

  Thread A: MinerU content_list_v2 -> visual items (image/chart/table/
            diagram) only, saved as image crops with page_idx metadata
            (no captioning at this stage).
  Thread B: PDF pages rendered via PyMuPDF -> whole-page text,
            transcribed by Qwen2.5-VL (see `captioning.qwen_vl.transcribe_page`).

The two streams share no common item-level ID (Thread B never sees
MinerU's content_list), so they're joined on `page_idx` alone."""

from dataclasses import asdict, dataclass

VISUAL_TYPES = {"image", "chart", "table", "diagram"}


@dataclass
class PageVisual:
    item_id: str
    type: str
    sub_type: str | None
    img_path: str
    relevance: str | None = None  # "semantic" | "decorative" | None (unclassified)


def visuals_by_page(
    content_list: list[dict],
    pdf_stem: str,
) -> dict[int, list[PageVisual]]:
    """Groups MinerU visual items (image/chart/table/diagram) by page_idx.
    No captioning — img_path is kept as-is for a later captioning stage."""
    pages: dict[int, list[PageVisual]] = {}

    for index, entry in enumerate(content_list):
        if entry.get("type") not in VISUAL_TYPES or not entry.get("img_path"):
            continue

        page_idx = entry.get("page_idx", -1)
        item_id = f"{pdf_stem}#p{page_idx}#{index}"

        pages.setdefault(page_idx, []).append(
            PageVisual(
                item_id=item_id,
                type=entry["type"],
                sub_type=entry.get("sub_type"),
                img_path=entry["img_path"],
            )
        )

    return pages


def render_merged_document(
    page_texts: dict[int, str],
    visuals: dict[int, list[PageVisual]],
) -> dict[str, dict]:
    """page_texts: page_idx -> transcribed text (Thread B output).
    visuals: page_idx -> list[PageVisual] (Thread A output, from
    `visuals_by_page`). Returns a page-grouped dict:
        {"<page_idx>": {"text": ..., "visuals": [{...}, ...]}}
    keyed by page_idx as a string (JSON object keys must be strings),
    sorted in page order."""
    page_indices = sorted(set(page_texts) | set(visuals))
    return {
        str(page_idx): {
            "text": page_texts.get(page_idx, ""),
            "visuals": [asdict(v) for v in visuals.get(page_idx, [])],
        }
        for page_idx in page_indices
    }
