"""
Dynamic OCR text region merging with grammatical continuation heuristics.
Supports horizontal multi-word merging and vertical multiline stacked label merging.
"""

import re
from typing import List, Optional
from .preprocessing import BoundingBox, OCRTextRegion

CONNECTING_WORDS = {
    "with", "and", "of", "to", "in", "for", "on", "at", "by", "from", "the", "a", "an", "or"
}

BULLET_CHARS = {"-", "•", "*", "–", "—"}


def is_continuation_line(upper_text: str, lower_text: str) -> bool:
    """
    Determine if lower_text is a grammatical continuation of upper_text.
    Continuation signals:
    1. lower_text starts with a lowercase character (e.g. 'duodenal papilla', 'ampulla with')
    2. lower_text starts with parenthesis/bracket enclosing lowercase text (e.g. '(of puborectalis)')
    3. upper_text ends with a connecting word (e.g. 'with', 'and', 'of', 'to') or punctuation (',', '-')

    Non-continuation signals:
    - lower_text starts with a bullet point / hyphen followed by uppercase (e.g. '-External oblique')
    - lower_text starts with uppercase letter and upper_text is a complete phrase
    """
    if not lower_text or not upper_text:
        return False

    l_stripped = lower_text.strip()
    u_stripped = upper_text.strip()

    if not l_stripped or not u_stripped:
        return False

    # Check for bullet point / dash prefix
    if l_stripped[0] in BULLET_CHARS:
        rest = l_stripped[1:].lstrip()
        if rest and rest[0].isalpha() and rest[0].islower():
            return True
        if rest and (rest[0].isupper() or rest[0].isdigit()):
            last_word = u_stripped.split()[-1].lower() if u_stripped.split() else ""
            last_word_clean = re.sub(r"[^\w]", "", last_word)
            if not u_stripped.endswith(("-", ",", ":")) and last_word_clean not in CONNECTING_WORDS:
                return False

    first_char = l_stripped[0]

    # Signal 1: Starts with lowercase letter
    if first_char.isalpha() and first_char.islower():
        return True

    # Signal 2: Starts with parenthesis, bracket, or brace
    if first_char in ("(", "[", "{"):
        if re.match(r"^[\(\[\{][A-Za-z0-9]{1,3}[\)\]\}]\s+[A-Z]", l_stripped):
            return False
        return True

    # Signal 3: Upper text ends with a connecting word or hyphen/comma/colon
    last_word = u_stripped.split()[-1].lower() if u_stripped.split() else ""
    last_word_clean = re.sub(r"[^\w]", "", last_word)
    if last_word_clean in CONNECTING_WORDS:
        return True

    if u_stripped.endswith(("-", ",", ":")):
        return True

    return False


def should_merge_horizontal(r1: OCRTextRegion, r2: OCRTextRegion, max_x_dist: float, max_y_diff: float) -> bool:
    """Determine if two OCR text regions are adjacent words on the same horizontal line."""
    c1, c2 = r1.bbox.center, r2.bbox.center

    if abs(c1.y - c2.y) > max_y_diff:
        return False

    if r1.bbox.x2 <= r2.bbox.x1:
        gap = r2.bbox.x1 - r1.bbox.x2
    elif r2.bbox.x2 <= r1.bbox.x1:
        gap = r1.bbox.x1 - r2.bbox.x2
    else:
        gap = 0.0

    return gap <= max_x_dist


def should_merge_vertical(
    r1: OCRTextRegion,
    r2: OCRTextRegion,
    max_x_offset: float = 40.0,
    max_y_gap_ratio: float = 1.2
) -> bool:
    """
    Determine if two vertically adjacent OCR text regions should be merged into a multiline label.
    Requires both geometric vertical proximity/alignment AND grammatical continuation validation.
    """
    # Sort into top and bottom region
    if r1.bbox.y1 <= r2.bbox.y1:
        top, bot = r1, r2
    else:
        top, bot = r2, r1

    y_gap = bot.bbox.y1 - top.bbox.y2
    max_allowed_gap = max_y_gap_ratio * max(top.bbox.height, bot.bbox.height)
    if y_gap > max_allowed_gap or y_gap < -0.5 * min(top.bbox.height, bot.bbox.height):
        return False

    # Horizontal alignment check
    x1_diff = abs(top.bbox.x1 - bot.bbox.x1)
    center_diff = abs(top.bbox.center.x - bot.bbox.center.x)
    x2_diff = abs(top.bbox.x2 - bot.bbox.x2)

    left_aligned = x1_diff <= max_x_offset
    center_aligned = center_diff <= max_x_offset
    right_aligned = x2_diff <= max_x_offset

    if not (left_aligned or center_aligned or right_aligned):
        return False

    return is_continuation_line(top.text, bot.text)


def merge_two_regions(r1: OCRTextRegion, r2: OCRTextRegion, join_str: str = " ") -> OCRTextRegion:
    """Merges two OCRTextRegion instances into a single bounding box and concatenated text."""
    if r1.bbox.y1 <= r2.bbox.y1:
        first, second = r1, r2
    elif abs(r1.bbox.y1 - r2.bbox.y1) < 5 and r1.bbox.x1 <= r2.bbox.x1:
        first, second = r1, r2
    else:
        first, second = r2, r1

    new_x1 = min(r1.bbox.x1, r2.bbox.x1)
    new_y1 = min(r1.bbox.y1, r2.bbox.y1)
    new_x2 = max(r1.bbox.x2, r2.bbox.x2)
    new_y2 = max(r1.bbox.y2, r2.bbox.y2)

    merged_text = f"{first.text.strip()}{join_str}{second.text.strip()}"
    merged_conf = min(r1.confidence, r2.confidence)

    poly = None
    if r1.poly and r2.poly:
        poly = r1.poly + r2.poly

    return OCRTextRegion(
        text=merged_text,
        bbox=BoundingBox(new_x1, new_y1, new_x2, new_y2),
        confidence=merged_conf,
        id=r1.id,
        poly=poly
    )


def merge_ocr_regions(
    regions: List[OCRTextRegion],
    max_x_dist_scale: float = 1.5,
    max_y_diff_scale: float = 0.5,
    max_x_offset: float = 45.0,
    max_y_gap_ratio: float = 1.2
) -> List[OCRTextRegion]:
    """
    Two-pass iterative merging algorithm for OCR text regions:
    Pass 1: Horizontal adjacent word merging.
    Pass 2: Vertical multiline label merging based on grammatical continuation.
    Assigns sequential numeric IDs (1, 2, 3...) to final merged regions.
    """
    if not regions:
        return []

    # Pass 1: Horizontal merging
    current_regions = list(regions)
    changed = True
    while changed:
        changed = False
        new_list = []
        skip_indices = set()

        for i in range(len(current_regions)):
            if i in skip_indices:
                continue
            r1 = current_regions[i]
            merged_r = r1
            for j in range(i + 1, len(current_regions)):
                if j in skip_indices:
                    continue
                r2 = current_regions[j]
                char_h = max(r1.bbox.height, r2.bbox.height)
                max_x = max_x_dist_scale * char_h
                max_y = max_y_diff_scale * char_h

                if should_merge_horizontal(merged_r, r2, max_x_dist=max_x, max_y_diff=max_y):
                    merged_r = merge_two_regions(merged_r, r2, join_str=" ")
                    skip_indices.add(j)
                    changed = True

            new_list.append(merged_r)
        current_regions = new_list

    # Pass 2: Vertical merging
    changed = True
    while changed:
        changed = False
        new_list = []
        skip_indices = set()

        for i in range(len(current_regions)):
            if i in skip_indices:
                continue
            r1 = current_regions[i]
            merged_r = r1
            for j in range(i + 1, len(current_regions)):
                if j in skip_indices:
                    continue
                r2 = current_regions[j]
                if should_merge_vertical(merged_r, r2, max_x_offset=max_x_offset, max_y_gap_ratio=max_y_gap_ratio):
                    merged_r = merge_two_regions(merged_r, r2, join_str=" ")
                    skip_indices.add(j)
                    changed = True

            new_list.append(merged_r)
        current_regions = new_list

    # Re-index remaining merged regions sequentially 1...N
    final_regions = []
    for idx, reg in enumerate(current_regions, start=1):
        reg.id = idx
        final_regions.append(reg)

    return final_regions


# Alias for backward compatibility
merge_ocr_labels = merge_ocr_regions
