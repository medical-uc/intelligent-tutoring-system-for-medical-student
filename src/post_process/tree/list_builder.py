import re
from typing import List, Tuple, Optional
from post_process.models.block import Block
from post_process.models.node import Node, NodeType

BULLET_PREFIX_REGEX = re.compile(r"^\s*([-*•–—○▪■]|\d+[\.\)]|[a-zA-Z][\.\)])\s*")
BULLET_CHAR_REGEX = re.compile(r"^\s*[-*•–—○▪■]\s+")
NUMBER_PREFIX_REGEX = re.compile(r"^\s*(\d+)[\.\)]\s*")
INDENT_THRESHOLD = 8.0

def clean_list_text(text: str) -> str:
    cleaned = BULLET_PREFIX_REGEX.sub("", text).strip()
    return cleaned if cleaned else text.strip()

def extract_item_number(text: str) -> Optional[int]:
    m = NUMBER_PREFIX_REGEX.match(text)
    if m:
        return int(m.group(1))
    return None

def has_explicit_bullet_symbol(text: str) -> bool:
    return bool(BULLET_CHAR_REGEX.match(text))

def patch_missing_sequence_numbers(blocks: List[Block]):
    """
    Scans blocks per page for ordered list sequences with OCR-truncated item numbers
    (e.g., 1. Mucosa, 2. Submucosa, [missing 3.] Cartilaginous layer, 4. Adventitia)
    and restores missing sequence number prefixes if intermediate blocks match the left margin.
    """
    page_map = {}
    for b in blocks:
        page_map.setdefault(b.page, []).append(b)

    for p_num, p_blocks in page_map.items():
        nums_found = []
        for idx, b in enumerate(p_blocks):
            num = extract_item_number(b.text)
            if num is not None:
                nums_found.append((idx, num, b))

        for i in range(len(nums_found) - 1):
            idx_a, num_a, block_a = nums_found[i]
            idx_b, num_b, block_b = nums_found[i + 1]
            gap = num_b - num_a
            num_between = idx_b - idx_a - 1
            if gap > 1 and num_between == (gap - 1):
                for step, k in enumerate(range(idx_a + 1, idx_b), start=1):
                    missing_num = num_a + step
                    target_b = p_blocks[k]
                    if abs(target_b.bbox[0] - block_a.bbox[0]) <= 15:
                        target_b.text = f"{missing_num}. {target_b.text}"
                        target_b.semantic_type = "LIST_ITEM"


class ListStackHandler:
    """
    Manages nested list items within a container (SLIDE/SUBHEADING).
    Supports:
    - Numbered list continuation across columns/text boxes (e.g. 1. CCA -> 2. IJV -> 3. CN X).
    - Automatic sub-item nesting (Level 2) under Level 1 numbered items for non-numbered items.
    - Relative x0 horizontal indentation steps for multi-level nesting.
    """
    def __init__(self, container: Node):
        self.container = container
        # Stack entries: (item_node, x0_coord, indent_level, parent_list_node)
        self.stack: List[Tuple[Node, float, int, Node]] = []
        self.root_ordered_list: Optional[Node] = None

    def reset(self):
        self.stack.clear()
        self.root_ordered_list = None

    def add_block(self, block: Block) -> Node:
        text = block.text.strip()
        item_num = extract_item_number(text)
        is_ordered = item_num is not None
        cleaned_text = clean_list_text(text)

        x0 = block.bbox[0] if block.bbox and len(block.bbox) >= 1 else 0.0

        # Case 1: Numbered item continuation (e.g., 2. IJV, 3. CN X, 4. Adventitia)
        if is_ordered and item_num is not None and item_num > 1 and self.root_ordered_list is not None:
            item_node = Node(
                id=f"{block.id}_item",
                type=NodeType.LIST_ITEM,
                text=cleaned_text,
                bbox=block.bbox,
                page=block.page,
                level=1,
            )
            self.root_ordered_list.add_child(item_node)
            self.stack = [(item_node, x0, 1, self.root_ordered_list)]
            return item_node

        # Case 2: Unordered item following Level 1 ordered item -> Nest as Level 2 under current ordered item!
        if self.root_ordered_list is not None and not is_ordered and self.stack and self.stack[-1][2] == 1:
            top_item, top_x0, top_level, top_list = self.stack[-1]
            nested_list = Node(
                id=f"{block.id}_list_l2",
                type=NodeType.LIST,
                page=block.page,
                level=2,
                ordered=False,
            )
            top_item.add_child(nested_list)

            item_node = Node(
                id=f"{block.id}_item",
                type=NodeType.LIST_ITEM,
                text=cleaned_text,
                bbox=block.bbox,
                page=block.page,
                level=2,
            )
            nested_list.add_child(item_node)
            self.stack.append((item_node, x0, 2, nested_list))
            return item_node

        # Case 3: First item in container / column
        if not self.stack:
            root_list = Node(
                id=f"{block.id}_list",
                type=NodeType.LIST,
                page=block.page,
                level=1,
                ordered=is_ordered,
            )
            if is_ordered:
                self.root_ordered_list = root_list
            self.container.add_child(root_list)

            item_node = Node(
                id=f"{block.id}_item",
                type=NodeType.LIST_ITEM,
                text=cleaned_text,
                bbox=block.bbox,
                page=block.page,
                level=1,
            )
            root_list.add_child(item_node)
            self.stack.append((item_node, x0, 1, root_list))
            return item_node

        # Case 4: Significant horizontal displacement without list continuation
        if self.stack and abs(x0 - self.stack[0][1]) > 80.0 and not is_ordered:
            self.stack.clear()
            return self.add_block(block)

        # Case 5: Deeper indentation (> top_x0 + INDENT_THRESHOLD)
        top_item, top_x0, top_level, top_list = self.stack[-1]
        if x0 > top_x0 + INDENT_THRESHOLD:
            new_level = top_level + 1
            nested_list = Node(
                id=f"{block.id}_list_l{new_level}",
                type=NodeType.LIST,
                page=block.page,
                level=new_level,
                ordered=is_ordered,
            )
            top_item.add_child(nested_list)

            item_node = Node(
                id=f"{block.id}_item",
                type=NodeType.LIST_ITEM,
                text=cleaned_text,
                bbox=block.bbox,
                page=block.page,
                level=new_level,
            )
            nested_list.add_child(item_node)
            self.stack.append((item_node, x0, new_level, nested_list))
            return item_node

        # Case 6: Pop stack until matching indentation is found
        while len(self.stack) > 1 and x0 < self.stack[-1][1] - INDENT_THRESHOLD:
            self.stack.pop()

        top_item, top_x0, top_level, top_list = self.stack[-1]

        item_node = Node(
            id=f"{block.id}_item",
            type=NodeType.LIST_ITEM,
            text=cleaned_text,
            bbox=block.bbox,
            page=block.page,
            level=top_level,
        )
        top_list.add_child(item_node)
        self.stack[-1] = (item_node, x0, top_level, top_list)
        return item_node
