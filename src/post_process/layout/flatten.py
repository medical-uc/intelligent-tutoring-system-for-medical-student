import re
from typing import List, Dict, Any
from post_process.models.block import Block

def _extract_spans_text_and_media(lines: List[Dict[str, Any]]):
    text_parts = []
    spans_list = []
    html_content = None

    for line in lines:
        line_spans = line.get("spans", [])
        line_text_parts = []
        for span in line_spans:
            spans_list.append(span)
            content = span.get("content")
            if content:
                line_text_parts.append(str(content))
            if not html_content and span.get("html"):
                html_content = span.get("html")

        if line_text_parts:
            text_parts.append(" ".join(line_text_parts))

    # Merge all lines in a block by SPACE (not newline!) and normalize spaces
    full_text = " ".join(text_parts).strip()
    full_text = re.sub(r"\s+", " ", full_text)
    return full_text, spans_list, html_content


def flatten_layout(pages: List[Dict[str, Any]]) -> List[Block]:
    """
    Stage 2 — Flatten Layout Tree
    Convert nested MinerU structures into a flat list of semantic blocks.
    - Merge lines by space
    - Omit images (user request)
    - Preserve list container context
    """
    flat_blocks: List[Block] = []
    block_counter = 0

    for page in pages:
        page_idx = page.get("page_idx", 0)
        raw_para_blocks = page.get("para_blocks")
        if not raw_para_blocks:
            raw_para_blocks = page.get("preproc_blocks", [])

        for top_block in raw_para_blocks:
            if not isinstance(top_block, dict):
                continue

            top_type = (top_block.get("type") or "text").lower()

            # Ignore image blocks per user request
            if top_type in ("image", "image_body", "image_caption"):
                continue

            nested_sub_blocks = top_block.get("blocks")

            if nested_sub_blocks and isinstance(nested_sub_blocks, list):
                for sub in nested_sub_blocks:
                    if not isinstance(sub, dict):
                        continue
                    sub_type = (sub.get("type") or top_type).lower()

                    if sub_type in ("image", "image_body", "image_caption"):
                        continue

                    # If container is list, preserve raw_type as list
                    effective_type = "list" if top_type == "list" else sub_type

                    block_counter += 1
                    bbox = sub.get("bbox", top_block.get("bbox", [0, 0, 0, 0]))
                    lines = sub.get("lines", [])
                    text, spans, html = _extract_spans_text_and_media(lines)

                    if not html and sub.get("html"):
                        html = sub.get("html")

                    block_id = f"p{page_idx}_b{block_counter}"
                    flat_blocks.append(Block(
                        id=block_id,
                        page=page_idx,
                        bbox=bbox,
                        text=text,
                        raw_type=effective_type,
                        spans=spans,
                        html=html,
                        image_path=None
                    ))
            else:
                block_counter += 1
                bbox = top_block.get("bbox", [0, 0, 0, 0])
                lines = top_block.get("lines", [])
                text, spans, html = _extract_spans_text_and_media(lines)

                if not html and top_block.get("html"):
                    html = top_block.get("html")

                block_id = f"p{page_idx}_b{block_counter}"
                flat_blocks.append(Block(
                    id=block_id,
                    page=page_idx,
                    bbox=bbox,
                    text=text,
                    raw_type=top_type,
                    spans=spans,
                    html=html,
                    image_path=None
                ))

    return flat_blocks
