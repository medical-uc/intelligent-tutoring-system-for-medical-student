import re
from html.parser import HTMLParser
from typing import List

from ..models.node import Node, NodeType


class TableHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: List[List[str]] = []
        self.current_row: List[str] = []
        self.current_cell: List[str] = []
        self.in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.current_row = []
        elif tag in ("td", "th"):
            self.in_cell = True
            self.current_cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self.in_cell = False
            cell_text = "".join(self.current_cell).strip().replace("\n", " ")
            cell_text = re.sub(r"\s+", " ", cell_text)
            self.current_row.append(cell_text)
        elif tag == "tr":
            if self.current_row:
                self.rows.append(self.current_row)

    def handle_data(self, data):
        if self.in_cell:
            self.current_cell.append(data)


def html_table_to_markdown(html_str: str) -> str:
    if not html_str:
        return ""
    try:
        parser = TableHTMLParser()
        parser.feed(html_str)
        rows = parser.rows
        if not rows:
            return ""

        max_cols = max(len(r) for r in rows)
        if max_cols == 0:
            return ""

        md_lines = []
        header = rows[0] + [""] * (max_cols - len(rows[0]))
        header_str = "| " + " | ".join(c.replace("|", "\\|") for c in header) + " |"
        sep_str = "| " + " | ".join(["---"] * max_cols) + " |"
        md_lines.append(header_str)
        md_lines.append(sep_str)

        for r in rows[1:]:
            padded_row = r + [""] * (max_cols - len(r))
            row_str = (
                "| " + " | ".join(c.replace("|", "\\|") for c in padded_row) + " |"
            )
            md_lines.append(row_str)

        return "\n".join(md_lines)
    except Exception:
        return ""


def render_node(node: Node, indent_level: int = 0) -> str:
    output = []
    indent_prefix = "  " * max(0, indent_level)

    if node.type == NodeType.DOCUMENT:
        if node.title:
            output.append(f"# {node.title}\n\n")
        for child in node.children:
            output.append(render_node(child, 0))

    elif node.type == NodeType.SECTION:
        if node.title:
            output.append(f"## {node.title}\n\n")
        for child in node.children:
            output.append(render_node(child, 0))

    elif node.type == NodeType.SLIDE:
        if node.title and not node.title.lower().startswith("slide "):
            output.append(f"### {node.title}\n\n")
        for child in node.children:
            output.append(render_node(child, 0))

    elif node.type == NodeType.SUBHEADING:
        if node.title:
            output.append(f"#### {node.title}\n\n")
        for child in node.children:
            output.append(render_node(child, 0))

    elif node.type == NodeType.PARAGRAPH:
        if node.text:
            output.append(f"{node.text}\n\n")

    elif node.type == NodeType.LIST:
        for idx, child in enumerate(node.children):
            if child.type == NodeType.LIST_ITEM:
                bullet = f"{idx + 1}." if node.ordered else "-"
                output.append(f"{indent_prefix}{bullet} {child.text}\n")
                # Render nested lists inside list item if any
                for sub in child.children:
                    output.append(render_node(sub, indent_level + 1))
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

    elif node.type == NodeType.IMAGE:
        # Images omitted per user request
        pass

    return "".join(output)


def render_markdown(tree_root: Node) -> str:
    """
    Stage 8 — Markdown Rendering
    Traverse the semantic tree recursively and return formatted Markdown.
    """
    raw_md = render_node(tree_root)
    cleaned = re.sub(r"\n{3,}", "\n\n", raw_md)
    return cleaned.strip() + "\n"
