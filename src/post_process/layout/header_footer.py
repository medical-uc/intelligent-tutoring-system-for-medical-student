import re
from typing import List, Dict, Set, Optional
from post_process.models.block import Block

PAGE_NUM_REGEX = re.compile(r"^\s*(?:page\s*)?\d+(?:\s*/\s*\d+|\s+of\s+\d+)?\s*$", re.IGNORECASE)

def remove_headers_footers(blocks: List[Block], min_repeats: Optional[int] = None) -> List[Block]:
    """
    Stage 4 — Header/Footer Detection
    Identify true running headers/footers (page numbers, tiny template headers) across pages and remove them.
    Protects legitimate slide titles from accidental deletion.
    """
    if not blocks:
        return blocks

    pages_present = len({b.page for b in blocks})
    if min_repeats is None:
        min_repeats = 2 if pages_present <= 3 else 5

    # Gather texts appearing at extreme edges (< 0.05 or > 0.92) across multiple pages
    header_texts: Dict[str, Set[int]] = {}
    footer_texts: Dict[str, Set[int]] = {}

    for block in blocks:
        feat = block.features
        text = block.text.strip().lower()
        if not feat or not text:
            continue

        # Protect slide titles and main headings from being treated as headers
        if block.raw_type == "title" or feat.font_size >= feat.average_font:
            continue

        if feat.top_ratio < 0.05:
            header_texts.setdefault(text, set()).add(block.page)
        elif feat.top_ratio > 0.92:
            footer_texts.setdefault(text, set()).add(block.page)

    repeated_headers = {text for text, pages in header_texts.items() if len(pages) >= min_repeats}
    repeated_footers = {text for text, pages in footer_texts.items() if len(pages) >= min_repeats}

    filtered_blocks: List[Block] = []

    for block in blocks:
        feat = block.features
        text_clean = block.text.strip()
        text_lower = text_clean.lower()

        if not feat:
            filtered_blocks.append(block)
            continue

        # Page number check at extreme edges
        if (feat.top_ratio < 0.08 or feat.top_ratio > 0.90) and PAGE_NUM_REGEX.match(text_clean):
            continue

        # Header check
        if feat.top_ratio < 0.05 and text_lower in repeated_headers:
            continue

        # Footer check
        if feat.top_ratio > 0.92 and text_lower in repeated_footers:
            continue

        filtered_blocks.append(block)

    return filtered_blocks
