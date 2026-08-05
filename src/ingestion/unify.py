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

import re
from dataclasses import dataclass
from html.parser import HTMLParser

VISUAL_TYPES = {"image", "chart", "table", "diagram"}
TEXT_FIELD_TYPES = {"text", "header", "footer", "aside_text"}

# MinerU emits stray "<TAG>▪</TAG> " as a bullet marker on plain-text lines
# (not inside a "list" item) — promote it to a real markdown bullet instead
# of leaving the tag in place. Seen as <sup> so far, but written generically
# in case other wrapper tags carry the same glyph.
_ANY_BULLET_RE = re.compile(r"^<([a-zA-Z0-9]+)[^>]*>[▪•]</\1>\s*")
# Any other inline tag (<sup>, <sub>, <b>, ...): drop the tag, keep the text.
# <sup> specifically usually marks a genuine superscript (ordinals like
# "2nd", arrows) so render it as a caret-superscript; everything else just
# loses its markup.
_ANY_TAG_RE = re.compile(
    r"<(sup|sub|b|i|em|strong|u|span)\b[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL
)
_LEFTOVER_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(\s[^>]*)?>")


def _tag_replacement(match: re.Match) -> str:
    tag, inner = match.group(1).lower(), match.group(2)
    return f"^{inner}" if tag == "sup" else inner


def _clean_inline_html(text: str) -> str:
    text = _ANY_BULLET_RE.sub("- ", text)
    text = _ANY_TAG_RE.sub(_tag_replacement, text)
    text = _LEFTOVER_TAG_RE.sub("", text)  # safety net for any unhandled tag
    return text


class _TableToMarkdown(HTMLParser):
    """Minimal <table>/<tr>/<td>/<th> -> GitHub-flavored markdown table
    converter. MinerU table_body is always this simple shape (no nested
    tags, no rowspan/colspan handling needed for the tables seen so far)."""

    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None:
            self._row.append(_clean_inline_html("".join(self._cell)).strip())
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _html_table_to_markdown(table_body: str) -> str:
    parser = _TableToMarkdown()
    parser.feed(table_body)
    rows = parser.rows
    if not rows:
        return ""

    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]

    lines = [
        "| " + " | ".join(rows[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines += ["| " + " | ".join(row) + " |" for row in rows[1:]]
    return "\n".join(lines)


@dataclass
class UnifiedItem:
    item_id: str
    page_idx: int
    content_type: str
    text: str  # rendered text: prose, list joined, table_body, or [FIGURE: ...] marker
    text_level: int | None = None  # heading level for "text" items, if any


def make_item_id(pdf_stem: str, page_idx: int, index: int) -> str:
    return f"{pdf_stem}#p{page_idx}#{index}"


def _render_text_item(item: dict) -> str:
    return item.get("text", "")


def _render_list_item(item: dict) -> str:
    return "\n".join(f"- {li}" for li in item.get("list_items", []))


def _clean_list_line(line: str) -> str:
    # list_items already get their own "- " prefix from _render_list_item;
    # strip a redundant leading bullet glyph tag instead of turning it into
    # a second "- ".
    prefix, rest = line[:2], line[2:]
    rest = _ANY_BULLET_RE.sub("", rest)
    rest = _ANY_TAG_RE.sub(_tag_replacement, rest)
    rest = _LEFTOVER_TAG_RE.sub("", rest)
    return prefix + rest


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

        text_level = None
        if content_type in TEXT_FIELD_TYPES:
            text = _render_text_item(entry)
            text_level = entry.get("text_level")
        elif content_type == "list":
            text = _render_list_item(entry)
        elif content_type == "table":
            caption = captions.get(item_id)
            text = (
                _render_visual_item(item_id, caption)
                if caption
                else _render_table_item(entry)
            )
        elif content_type in VISUAL_TYPES:
            text = _render_visual_item(item_id, captions.get(item_id))
        else:
            continue  # page_number and other non-content types

        if not text:
            continue

        items.append(
            UnifiedItem(
                item_id=item_id,
                page_idx=page_idx,
                content_type=content_type,
                text=text,
                text_level=text_level,
            )
        )

    return items


def render_unified_text(items: list[UnifiedItem]) -> str:
    return "\n\n".join(item.text for item in items)


def _render_markdown_block(item: UnifiedItem) -> str:
    if item.content_type == "table" and "<table>" in item.text:
        return _html_table_to_markdown(item.text)
    if item.content_type in TEXT_FIELD_TYPES and item.text_level:
        return f"{'#' * min(item.text_level, 6)} {_clean_inline_html(item.text)}"
    if item.content_type in VISUAL_TYPES:
        return f"> {_clean_inline_html(item.text)}"
    if item.content_type == "list":
        return "\n".join(_clean_list_line(line) for line in item.text.split("\n"))
    return _clean_inline_html(item.text)


def render_unified_markdown(items: list[UnifiedItem]) -> str:
    """Same content as render_unified_text, with headings promoted to `#`
    and figure/table captions rendered as blockquotes so the output reads
    as proper Markdown instead of plain prose."""
    return "\n\n".join(_render_markdown_block(item) for item in items)


def build_unified_items_from_merged(
    merged_document: dict[str, dict],
    captions: dict[str, str],
) -> list[UnifiedItem]:
    """Same output shape as build_unified_items, but built from a
    dual-pipeline merged document (page_idx -> {"text", "visuals"}, see
    `merge_by_page.render_merged_document`) instead of a MinerU
    content_list. Page text is Qwen-transcribed Markdown (already
    heading-formatted), emitted as a single text item per page.

    Visuals with relevance != "semantic" are dropped entirely (not just
    left uncaptioned) — decorative images add no value to the unified
    document and would otherwise appear as bare [FIGURE:...] markers
    with no caption.

    captions: item_id -> caption text, keyed by each visual's own
    item_id (already stable from the original content_list, reused
    as-is by `merge_by_page.visuals_by_page` — no regeneration needed).
    """
    items = []

    for page_idx in sorted(merged_document, key=int):
        page = merged_document[page_idx]

        text = page.get("text", "")
        if text:
            items.append(
                UnifiedItem(
                    item_id=f"page{page_idx}#text",
                    page_idx=int(page_idx),
                    content_type="text",
                    text=text,
                )
            )

        for visual in page.get("visuals", []):
            if visual.get("relevance") != "semantic":
                continue
            item_id = visual["item_id"]
            items.append(
                UnifiedItem(
                    item_id=item_id,
                    page_idx=int(page_idx),
                    content_type=visual["type"],
                    text=_render_visual_item(item_id, captions.get(item_id)),
                )
            )

    return items


def build_unified_items_from_postprocessed(
    postprocessed_document: dict,
    visuals_by_page_idx: dict[int, list],
    captions: dict[str, str],
) -> list[UnifiedItem]:
    """Same output shape as build_unified_items, but built from
    `post_process.pipeline.process_mineru_json` output (document_title,
    total_pages, pages: [{page_num (1-indexed), section, title, text,
    nodes}]) plus visuals extracted separately via
    `middle_visuals.extract_visuals` (page_idx, 0-indexed) — post_process
    drops image blocks by design, so visuals are joined back here by
    page number instead of coming from the same tree.

    visuals_by_page_idx: 0-indexed page_idx -> list[MiddleVisual].
    Visuals with relevance != "semantic" are dropped entirely, same as
    build_unified_items_from_merged.

    captions: item_id -> caption text, keyed by each visual's own
    item_id (see middle_visuals.extract_visuals).
    """
    items = []

    for page in postprocessed_document.get("pages", []):
        page_num = page["page_num"]
        page_idx = page_num - 1

        text = page.get("text", "")
        if text:
            items.append(
                UnifiedItem(
                    item_id=f"page{page_idx}#text",
                    page_idx=page_idx,
                    content_type="text",
                    text=text,
                )
            )

        for visual in visuals_by_page_idx.get(page_idx, []):
            if visual.relevance != "semantic":
                continue
            items.append(
                UnifiedItem(
                    item_id=visual.item_id,
                    page_idx=page_idx,
                    content_type=visual.type,
                    text=_render_visual_item(
                        visual.item_id, captions.get(visual.item_id)
                    ),
                )
            )

    return items


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
        index
        for index, entry in enumerate(content_list)
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
