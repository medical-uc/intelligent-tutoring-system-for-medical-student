"""Builds a unified text document from a MinerU2.5 content_list.json,
inlining Stage 1 visual captions in place of each figure/table.

Provenance is carried through a stable item_id assigned per content_list
entry: f"{pdf_stem}#p{page_idx}#{index_in_list}". This ties three things
together without inventing new infra:
  - image -> page: page_idx stored on the UnifiedItem
  - caption -> image: same item_id key in the captions dict
  - position in document: list order (and bbox, if finer placement is
    ever needed)
"""

from dataclasses import dataclass

VISUAL_TYPES = {"image", "chart", "table", "diagram"}
TEXT_FIELD_TYPES = {"text", "header", "footer", "aside_text"}


@dataclass
class UnifiedItem:
    item_id: str
    page_idx: int
    content_type: str
    text: str  # rendered text: prose, list joined, table_body, or [FIGURE: ...] marker


def make_item_id(pdf_stem: str, page_idx: int, index: int) -> str:
    return f"{pdf_stem}#p{page_idx}#{index}"


def _render_text_item(item: dict) -> str:
    return item.get("text", "")


def _render_list_item(item: dict) -> str:
    return "\n".join(f"- {li}" for li in item.get("list_items", []))


def _render_table_item(item: dict) -> str:
    return item.get("table_body", "")


def _render_visual_item(item_id: str, caption: str | None) -> str:
    if caption:
        return f"[FIGURE:{item_id}] {caption}"
    return f"[FIGURE:{item_id}] (no caption available)"


def build_unified_items(
    content_list: list[dict],
    pdf_stem: str,
    captions: dict[str, str],
) -> list[UnifiedItem]:
    """captions: item_id -> caption text (see make_item_id for the key format)."""
    items = []

    for index, entry in enumerate(content_list):
        content_type = entry.get("type")
        page_idx = entry.get("page_idx", -1)
        item_id = make_item_id(pdf_stem, page_idx, index)

        if content_type in TEXT_FIELD_TYPES:
            text = _render_text_item(entry)
        elif content_type == "list":
            text = _render_list_item(entry)
        elif content_type == "table":
            caption = captions.get(item_id)
            text = _render_visual_item(item_id, caption) if caption else _render_table_item(entry)
        elif content_type in VISUAL_TYPES:
            text = _render_visual_item(item_id, captions.get(item_id))
        else:
            continue  # page_number and other non-content types

        if not text:
            continue

        items.append(UnifiedItem(item_id=item_id, page_idx=page_idx, content_type=content_type, text=text))

    return items


def render_unified_text(items: list[UnifiedItem]) -> str:
    return "\n\n".join(item.text for item in items)


def captions_by_item_id(
    content_list: list[dict],
    pdf_stem: str,
    caption_results: list[dict],
) -> dict[str, str]:
    """Maps Stage 1 caption results (keyed by image_path, from the
    notebook's batch run) back to item_id, by re-walking content_list in
    the same order used to produce them.

    caption_results: list of {"image_path": ..., "caption": ...} dicts,
    one per visual item in content_list, in content_list order.
    """
    visual_indices = [
        index for index, entry in enumerate(content_list)
        if entry.get("type") in VISUAL_TYPES and entry.get("img_path")
    ]

    if len(visual_indices) != len(caption_results):
        raise ValueError(
            f"content_list has {len(visual_indices)} visual items but got "
            f"{len(caption_results)} caption results — they must correspond "
            f"1:1 in order."
        )

    mapping = {}
    for index, result in zip(visual_indices, caption_results):
        entry = content_list[index]
        item_id = make_item_id(pdf_stem, entry.get("page_idx", -1), index)
        mapping[item_id] = result["caption"]

    return mapping
