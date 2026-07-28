from typing import List, Dict, Optional
from post_process.models.block import Block
from post_process.models.node import Node, NodeType
from post_process.tree.list_builder import ListStackHandler, patch_missing_sequence_numbers

def build_semantic_tree(blocks: List[Block]) -> Node:
    """
    Stage 7 — Semantic Tree Construction
    Construct document hierarchy tree from ordered classified blocks.
    Hierarchy:
    DOCUMENT -> SECTION -> SLIDE -> SUBHEADING -> [PARAGRAPH, LIST, TABLE, CAPTION]
    """
    # Pre-pass: patch any missing sequence numbers in ordered lists (e.g. 1, 2, [missing 3], 4)
    patch_missing_sequence_numbers(blocks)

    doc_node = Node(id="doc_root", type=NodeType.DOCUMENT, title="")

    current_section: Optional[Node] = None
    current_slide: Optional[Node] = None
    current_subheading: Optional[Node] = None

    container_list_handlers: Dict[str, ListStackHandler] = {}

    def reset_list_handler(container: Optional[Node]):
        if container and container.id in container_list_handlers:
            container_list_handlers[container.id].reset()

    def get_or_create_section(title: str = "") -> Node:
        nonlocal current_section, current_slide, current_subheading
        if current_section is None or title:
            current_section = Node(
                id=f"sec_{len(doc_node.children)+1}",
                type=NodeType.SECTION,
                title=title,
            )
            doc_node.add_child(current_section)
            current_slide = None
            current_subheading = None
        return current_section

    def get_or_create_slide(page_num: int, title: str = "") -> Node:
        nonlocal current_slide, current_subheading
        sec = get_or_create_section()
        if current_slide is None or current_slide.page != page_num:
            current_slide = Node(
                id=f"slide_p{page_num}",
                type=NodeType.SLIDE,
                title=title,
                page=page_num,
            )
            sec.add_child(current_slide)
            current_subheading = None
        elif title and not current_slide.title:
            current_slide.title = title

        return current_slide

    def get_list_handler(container: Node) -> ListStackHandler:
        if container.id not in container_list_handlers:
            container_list_handlers[container.id] = ListStackHandler(container)
        return container_list_handlers[container.id]

    for block in blocks:
        stype = block.semantic_type or "PARAGRAPH"
        text = block.text.strip()
        if not text and stype not in ("IMAGE", "TABLE"):
            continue

        if stype == "DOCUMENT_TITLE":
            if not doc_node.title:
                doc_node.title = text
            get_or_create_slide(block.page, title=text)

        elif stype == "SECTION_TITLE":
            if current_section and current_section.page == block.page and current_section.title:
                current_section.title = f"{current_section.title} {text}".strip()
            else:
                current_section = get_or_create_section(title=text)
                current_section.page = block.page

        elif stype == "SLIDE_TITLE":
            slide = get_or_create_slide(block.page, title=text)

        elif stype == "SUBHEADING":
            slide = get_or_create_slide(block.page)
            sub_node = Node(
                id=f"sub_{block.id}",
                type=NodeType.SUBHEADING,
                title=text,
                bbox=block.bbox,
                page=block.page,
            )
            slide.add_child(sub_node)
            current_subheading = sub_node
            reset_list_handler(slide)

        elif stype == "LIST_ITEM":
            slide = get_or_create_slide(block.page)
            container = current_subheading if current_subheading else slide
            handler = get_list_handler(container)
            handler.add_block(block)

        elif stype == "TABLE":
            slide = get_or_create_slide(block.page)
            container = current_subheading if current_subheading else slide
            tbl_node = Node(
                id=f"tbl_{block.id}",
                type=NodeType.TABLE,
                text=text,
                bbox=block.bbox,
                page=block.page,
                html=block.html,
            )
            container.add_child(tbl_node)
            reset_list_handler(container)

        elif stype == "CAPTION":
            slide = get_or_create_slide(block.page)
            container = current_subheading if current_subheading else slide
            cap_node = Node(
                id=f"cap_{block.id}",
                type=NodeType.CAPTION,
                text=text,
                bbox=block.bbox,
                page=block.page,
            )
            container.add_child(cap_node)

        elif stype in ("IMAGE", "DECORATION"):
            continue

        else:  # PARAGRAPH or UNKNOWN
            slide = get_or_create_slide(block.page)
            container = current_subheading if current_subheading else slide
            handler = get_list_handler(container)

            # If currently inside an active ordered list, treat description paragraph as sub-description item!
            if handler.root_ordered_list is not None:
                handler.add_block(block)
            else:
                para_node = Node(
                    id=f"para_{block.id}",
                    type=NodeType.PARAGRAPH,
                    text=text,
                    bbox=block.bbox,
                    page=block.page,
                )
                container.add_child(para_node)
                reset_list_handler(container)

    return doc_node
