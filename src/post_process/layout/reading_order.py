from typing import List, Optional

from ..models.block import Block


def _merge_paragraphs(blocks: List[Block]) -> List[Block]:
    if not blocks:
        return blocks

    merged: List[Block] = []
    prev: Optional[Block] = None

    for block in blocks:
        if (
            prev is not None
            and prev.semantic_type == "PARAGRAPH"
            and block.semantic_type == "PARAGRAPH"
            and prev.page == block.page
        ):
            prev_text = prev.text.strip()
            # Merge contiguous paragraph blocks if prev doesn't end with terminal punctuation
            if prev_text and not prev_text[-1] in (".", "!", "?", ":"):
                prev.text = prev.text + " " + block.text
                prev.bbox = [
                    min(prev.bbox[0], block.bbox[0]),
                    min(prev.bbox[1], block.bbox[1]),
                    max(prev.bbox[2], block.bbox[2]),
                    max(prev.bbox[3], block.bbox[3]),
                ]
                prev.spans.extend(block.spans)
                continue

        merged.append(block)
        prev = block

    return merged


def reconstruct_reading_order(blocks: List[Block]) -> List[Block]:
    """
    Stage 6 — Reading Order Reconstruction
    Preserves MinerU's native layout reading order (left column top-to-bottom, then next column to the right).
    Merges contiguous continuation paragraphs.
    """
    if not blocks:
        return blocks

    # Preserve natural para_blocks order from MinerU while merging continuation paragraphs
    final_blocks = _merge_paragraphs(blocks)
    return final_blocks
