import re
from typing import Any, Dict, List

from ..models.block import Block
from ..models.feature import Feature

BULLET_REGEX = re.compile(r"^\s*[-*•–—○▪■]\s+")
NUMBER_REGEX = re.compile(r"^\s*(\d+|[a-zA-Z])[\.\)]\s+")


def _estimate_font_size(block: Block, height: float, num_lines: int) -> float:
    span_heights = []
    for span in block.spans:
        s_bbox = span.get("bbox")
        if s_bbox and len(s_bbox) == 4:
            sh = s_bbox[3] - s_bbox[1]
            if sh > 0:
                span_heights.append(sh)

    if span_heights:
        return sum(span_heights) / len(span_heights)

    return max(1.0, height / max(1, num_lines))


def extract_features(blocks: List[Block], pages: List[Dict[str, Any]]) -> List[Block]:
    """
    Stage 3 — Feature Extraction
    Generate reusable features for every block, including block-level features
    and slide-level context features.
    """
    # Create page size lookup dict
    page_sizes: Dict[int, tuple] = {}
    for page in pages:
        p_idx = page.get("page_idx", 0)
        size = page.get("page_size", [720.0, 405.0])  # default width x height
        page_sizes[p_idx] = (float(size[0]), float(size[1]))

    # Step 1: Compute block-level features per block
    page_blocks_map: Dict[int, List[Block]] = {}
    for block in blocks:
        page_w, page_h = page_sizes.get(block.page, (720.0, 405.0))
        if page_w <= 0:
            page_w = 720.0
        if page_h <= 0:
            page_h = 405.0

        x0, y0, x1, y1 = block.bbox
        width = max(0.0, x1 - x0)
        height = max(0.0, y1 - y0)
        center_x = (x0 + x1) / 2.0
        center_y = (y0 + y1) / 2.0

        text = block.text or ""
        lines = [line for line in text.splitlines() if line.strip()]
        num_lines = max(1, len(lines))
        num_words = len(text.split())

        font_size = _estimate_font_size(block, height, num_lines)

        clean_text = text.strip()
        is_all_caps = bool(clean_text.isupper() and len(clean_text) > 1)
        starts_with_bullet = bool(BULLET_REGEX.match(text))
        starts_with_number = bool(NUMBER_REGEX.match(text))
        ends_with_colon = clean_text.endswith(":")

        top_ratio = y0 / page_h
        left_ratio = x0 / page_w
        indentation = left_ratio

        feat = Feature(
            width=width,
            height=height,
            center_x=center_x,
            center_y=center_y,
            font_size=font_size,
            indentation=indentation,
            num_words=num_words,
            num_lines=num_lines,
            is_all_caps=is_all_caps,
            starts_with_bullet=starts_with_bullet,
            starts_with_number=starts_with_number,
            ends_with_colon=ends_with_colon,
            top_ratio=top_ratio,
            left_ratio=left_ratio,
        )
        block.features = feat
        page_blocks_map.setdefault(block.page, []).append(block)

    # Step 2: Compute slide-level stats and attach to features
    for p_idx, p_blocks in page_blocks_map.items():
        font_sizes = [b.features.font_size for b in p_blocks if b.features]
        largest_font = max(font_sizes) if font_sizes else 0.0
        average_font = (sum(font_sizes) / len(font_sizes)) if font_sizes else 0.0

        num_text = sum(
            1
            for b in p_blocks
            if b.raw_type not in ("image", "table", "image_body", "table_body")
        )
        num_images = sum(1 for b in p_blocks if b.raw_type in ("image", "image_body"))
        num_tables = sum(1 for b in p_blocks if b.raw_type in ("table", "table_body"))

        for b in p_blocks:
            if b.features:
                b.features.largest_font = largest_font
                b.features.average_font = average_font
                b.features.num_text_blocks = num_text
                b.features.num_images = num_images
                b.features.num_tables = num_tables

    return blocks
