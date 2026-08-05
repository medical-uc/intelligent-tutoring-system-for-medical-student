import re
from typing import Dict, List, Set

from ..models.block import Block
from .rules import score_block

SECTION_NUM_REGEX = re.compile(r"^\s*0?\d\s*$", re.IGNORECASE)


def classify_blocks(
    blocks: List[Block], confidence_threshold: float = 0.7
) -> List[Block]:
    """
    Stage 5 — Block Classification
    Detects section slides and assigns semantic types (DOCUMENT_TITLE, SECTION_TITLE, SLIDE_TITLE, SUBHEADING, LIST_ITEM, PARAGRAPH, TABLE).
    """
    # Map page -> blocks
    page_map: Dict[int, List[Block]] = {}
    for b in blocks:
        page_map.setdefault(b.page, []).append(b)

    # Detect section divider slides
    section_slides: Set[int] = set()
    for p_num, p_blocks in page_map.items():
        if p_num == 0:
            continue
        has_sec_num = any(SECTION_NUM_REGEX.match(b.text.strip()) for b in p_blocks)
        if has_sec_num and len(p_blocks) <= 5:
            section_slides.add(p_num)

    for p_num, p_blocks in page_map.items():
        is_sec_slide = p_num in section_slides
        first_title_found = False

        for block in p_blocks:
            text = block.text.strip()
            feat = block.features
            raw_type = (block.raw_type or "").lower()

            starts_with_bullet = (
                bool(feat and (feat.starts_with_bullet or feat.starts_with_number))
                or raw_type == "list"
            )
            is_candidate_title = not starts_with_bullet and (
                raw_type == "title" or (feat and feat.top_ratio < 0.38)
            )

            is_first_title = False
            if is_candidate_title and not first_title_found and not is_sec_slide:
                is_first_title = True
                first_title_found = True

            scores = score_block(
                block,
                is_first_title_on_page=is_first_title,
                is_section_slide=is_sec_slide,
            )

            best_type = "PARAGRAPH"
            best_score = 0.0

            for stype, score in scores.items():
                if score > best_score:
                    best_score = score
                    best_type = stype

            if best_score < confidence_threshold:
                block.semantic_type = "UNKNOWN"
                block.confidence = best_score
            else:
                block.semantic_type = best_type
                block.confidence = best_score

    return blocks
