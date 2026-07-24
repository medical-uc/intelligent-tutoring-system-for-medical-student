"""Converts MinerU2.5's `_content_list_v2.json` (page-grouped, nested
`content` objects) into the flat legacy-style schema consumed by
`ingestion.unify` (`text`/`text_level`, `img_path`, `table_body`, ...),
written out as `_content_list.clean.json`.

v2 -> legacy field mapping (per item, `page_idx` comes from the item's
position in the outer per-page grouping):
  title            -> text, text_level=content.level
  paragraph        -> text
  page_header      -> header (text)
  page_footer      -> footer (text)
  page_aside_text  -> aside_text (text)
  list             -> list (list_items: list[str])
  image            -> image (img_path, image_caption, image_footnote,
                      content, sub_type)
  table            -> table (img_path, table_caption, table_footnote,
                      table_body)
"""

import json
from pathlib import Path

_TEXT_CONTENT_KEYS = {
    "title": "title_content",
    "paragraph": "paragraph_content",
    "page_header": "page_header_content",
    "page_footer": "page_footer_content",
    "page_aside_text": "page_aside_text_content",
}

_TYPE_RENAME = {
    "title": "text",
    "paragraph": "text",
    "page_header": "header",
    "page_footer": "footer",
    "page_aside_text": "aside_text",
}


def _join_text_content(parts: list[dict]) -> str:
    return "".join(part.get("content", "") for part in parts)


def _render_list_item_content(item: dict) -> str:
    return _join_text_content(item.get("item_content", []))


def _convert_item(entry: dict, page_idx: int) -> dict:
    v2_type = entry["type"]
    content = entry.get("content", {})

    if v2_type in _TEXT_CONTENT_KEYS:
        parts = content.get(_TEXT_CONTENT_KEYS[v2_type], [])
        out = {
            "type": _TYPE_RENAME[v2_type],
            "text": _join_text_content(parts),
            "bbox": entry.get("bbox"),
            "page_idx": page_idx,
        }
        if v2_type == "title":
            out["text_level"] = content.get("level")
        return out

    if v2_type == "list":
        return {
            "type": "list",
            "sub_type": content.get("list_type"),
            "list_items": [
                _render_list_item_content(item)
                for item in content.get("list_items", [])
            ],
            "bbox": entry.get("bbox"),
            "page_idx": page_idx,
        }

    if v2_type == "image":
        return {
            "type": "image",
            "img_path": content.get("image_source", {}).get("path"),
            "image_caption": content.get("image_caption", []),
            "image_footnote": content.get("image_footnote", []),
            "content": content.get("content", ""),
            "sub_type": entry.get("sub_type"),
            "bbox": entry.get("bbox"),
            "page_idx": page_idx,
        }

    if v2_type == "table":
        return {
            "type": "table",
            "img_path": content.get("image_source", {}).get("path"),
            "table_caption": content.get("table_caption", []),
            "table_footnote": content.get("table_footnote", []),
            "table_body": content.get("html", ""),
            "bbox": entry.get("bbox"),
            "page_idx": page_idx,
        }

    raise ValueError(f"unrecognized content_list_v2 type: {v2_type!r}")


_DROPPED_TYPES = {"page_header", "page_footer"}


def convert_v2_to_clean(content_list_v2: list[list[dict]]) -> list[dict]:
    """content_list_v2: outer list is one entry per page, inner list is
    that page's items in reading order (the shape MinerU2.5 writes to
    `_content_list_v2.json`). Running headers/footers are dropped — they're
    page chrome, not document content."""
    return [
        _convert_item(entry, page_idx)
        for page_idx, page_items in enumerate(content_list_v2)
        for entry in page_items
        if entry["type"] not in _DROPPED_TYPES
    ]


def build_clean_content_list(content_list_v2_path: Path) -> Path:
    """Reads `{stem}_content_list_v2.json` and writes the flattened,
    legacy-schema `{stem}_content_list.clean.json` next to it."""
    with open(content_list_v2_path) as f:
        content_list_v2 = json.load(f)

    clean = convert_v2_to_clean(content_list_v2)

    stem = content_list_v2_path.name.removesuffix("_content_list_v2.json")
    clean_path = content_list_v2_path.with_name(f"{stem}_content_list.clean.json")
    with open(clean_path, "w") as f:
        json.dump(clean, f, indent=2)

    return clean_path
