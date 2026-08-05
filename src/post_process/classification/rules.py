import re
from typing import Dict

from ..models.block import Block

BULLET_PREFIX_PATTERN = re.compile(r"^\s*([-*•–—○▪■]|\d+[\.\)]|[a-zA-Z][\.\)])\s+")
SECTION_NUM_PATTERN = re.compile(
    r"^\s*(?:0?\d|section\s*\d+|part\s*[\d\w]+)\s*$", re.IGNORECASE
)
CAPTION_REGEX = re.compile(
    r"^\s*(?:figure|fig\.|table|source|credit)s?\s*[\d\.\:]*", re.IGNORECASE
)


def score_block(
    block: Block, is_first_title_on_page: bool = False, is_section_slide: bool = False
) -> Dict[str, float]:
    scores: Dict[str, float] = {
        "DOCUMENT_TITLE": 0.0,
        "SECTION_TITLE": 0.0,
        "SLIDE_TITLE": 0.0,
        "SUBHEADING": 0.0,
        "PARAGRAPH": 0.0,
        "LIST_ITEM": 0.0,
        "IMAGE": 0.0,
        "TABLE": 0.0,
        "CAPTION": 0.0,
        "DECORATION": 0.0,
    }

    raw_type = (block.raw_type or "").lower()
    text = (block.text or "").strip()
    feat = block.features

    # 1. Ignore images / image captions / slide corner label decorations
    if raw_type in ("image", "image_body", "image_caption"):
        scores["DECORATION"] = 1.0
        return scores

    if feat:
        # Corner tags / labels in far right bottom margin
        if feat.left_ratio > 0.80 and feat.top_ratio > 0.55 and feat.num_words <= 5:
            scores["DECORATION"] = 1.0
            return scores

    # 2. Table
    if raw_type in ("table", "table_body"):
        scores["TABLE"] = 1.0
        return scores

    # 3. List Item rule: any text starting with bullet/number or raw_type == 'list' IS a list item!
    starts_with_bullet = bool(
        feat and (feat.starts_with_bullet or feat.starts_with_number)
    ) or bool(BULLET_PREFIX_PATTERN.match(text))
    if starts_with_bullet or raw_type == "list":
        scores["LIST_ITEM"] = 1.0
        return scores

    # 4. Captions
    if raw_type in ("table_caption", "caption") or CAPTION_REGEX.match(text):
        scores["CAPTION"] = 0.95
        return scores

    if not feat:
        if raw_type == "title":
            scores["SLIDE_TITLE"] = 0.80
        else:
            scores["PARAGRAPH"] = 0.80
        return scores

    # 5. Document Title (Page 0 main title)
    if block.page == 0 and feat.top_ratio < 0.45:
        if feat.largest_font > 0 and feat.font_size >= feat.largest_font * 0.85:
            scores["DOCUMENT_TITLE"] = 0.98
            return scores

    # 6. Section Title (On section divider slides)
    if is_section_slide:
        if (
            raw_type == "title"
            or feat.font_size >= feat.average_font * 1.1
            or SECTION_NUM_PATTERN.match(text)
        ):
            scores["SECTION_TITLE"] = 0.95
            return scores

    # 7. Slide Title (Top non-bullet title block on content slides)
    if is_first_title_on_page and feat.top_ratio < 0.38:
        if raw_type == "title" or feat.font_size >= feat.average_font * 1.0:
            scores["SLIDE_TITLE"] = 0.95
            return scores

    # 8. Subheading (Secondary headings on a content slide)
    if raw_type == "title" or feat.font_size >= feat.average_font * 1.1:
        if feat.ends_with_colon or feat.is_all_caps or feat.num_words <= 12:
            scores["SUBHEADING"] = 0.90
            return scores

    # 9. Paragraph (Default for regular body text)
    scores["PARAGRAPH"] = 0.85
    return scores
