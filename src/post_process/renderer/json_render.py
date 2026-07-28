import re
from typing import Dict, Any, List, Optional
from ..models.node import Node, NodeType
from .markdown_render import html_table_to_markdown

def _render_node_text(node: Node, indent_level: int = 0) -> str:
    """Helper to render text for a node within a page/slide."""
    output = []
    indent_prefix = "  " * max(0, indent_level)

    if node.type == NodeType.SUBHEADING:
        if node.title:
            output.append(f"#### {node.title}\n\n")
        for child in node.children:
            output.append(_render_node_text(child, 0))

    elif node.type == NodeType.PARAGRAPH:
        if node.text:
            output.append(f"{node.text}\n\n")

    elif node.type == NodeType.LIST:
        for idx, child in enumerate(node.children):
            if child.type == NodeType.LIST_ITEM:
                bullet = f"{idx + 1}." if node.ordered else "-"
                output.append(f"{indent_prefix}{bullet} {child.text}\n")
                for sub in child.children:
                    output.append(_render_node_text(sub, indent_level + 1))
        output.append("\n")

    elif node.type == NodeType.TABLE:
        if node.html:
            md_tbl = html_table_to_markdown(node.html)
            if md_tbl:
                output.append(f"{md_tbl}\n\n")
            elif node.text:
                output.append(f"{node.text}\n\n")
        elif node.text:
            output.append(f"{node.text}\n\n")

    elif node.type == NodeType.CAPTION:
        if node.text:
            output.append(f"*{node.text}*\n\n")

    return "".join(output)


def node_to_dict(node: Node) -> Dict[str, Any]:
    """Convert a Node object into a serializable dictionary."""
    d: Dict[str, Any] = {
        "id": node.id,
        "type": node.type,
    }
    if node.title:
        d["title"] = node.title
    if node.text:
        d["text"] = node.text
    if node.page is not None:
        d["page"] = node.page + 1
    if node.bbox:
        d["bbox"] = node.bbox
    if node.html:
        d["html"] = node.html
    if node.children:
        d["children"] = [node_to_dict(c) for c in node.children]
    return d


def render_json(doc_root: Node, total_pages: Optional[int] = None) -> Dict[str, Any]:
    """
    Traverses the semantic tree and returns a structured JSON-serializable dictionary.
    
    Structure:
    {
        "document_title": "...",
        "total_pages": 214,
        "pages": [
            {
                "page_num": 1,
                "section": "...",
                "title": "...",
                "text": "...",
                "nodes": [...]
            }
        ]
    }
    """
    doc_title = doc_root.title or ""
    page_map: Dict[int, Dict[str, Any]] = {}
    max_page_seen = 0

    # Traverse sections and slides
    for sec_node in doc_root.children:
        sec_title = sec_node.title if sec_node.type == NodeType.SECTION else ""
        
        slides = sec_node.children if sec_node.type == NodeType.SECTION else [sec_node]
        for slide in slides:
            if slide.type == NodeType.SLIDE:
                p_num = (slide.page + 1) if slide.page is not None else 1
                max_page_seen = max(max_page_seen, p_num)

                
                slide_title = slide.title or ""
                
                # Render content text for slide
                text_parts = []
                for child in slide.children:
                    text_parts.append(_render_node_text(child))
                raw_text = "".join(text_parts)
                cleaned_text = re.sub(r"\n{3,}", "\n\n", raw_text).strip()

                page_map[p_num] = {
                    "page_num": p_num,
                    "section": sec_title,
                    "title": slide_title,
                    "text": cleaned_text,
                    "nodes": [node_to_dict(child) for child in slide.children]
                }

    doc_total_pages = total_pages if total_pages is not None else max(max_page_seen, 1)

    # Build dense ordered list of pages (1 .. doc_total_pages)
    pages_list = []
    for p in range(1, doc_total_pages + 1):
        if p in page_map:
            pages_list.append(page_map[p])
        else:
            pages_list.append({
                "page_num": p,
                "section": "",
                "title": "",
                "text": "",
                "nodes": []
            })

    return {
        "document_title": doc_title,
        "total_pages": doc_total_pages,
        "pages": pages_list
    }
