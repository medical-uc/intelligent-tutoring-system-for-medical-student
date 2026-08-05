from dataclasses import dataclass, field
from typing import List, Optional


class NodeType:
    DOCUMENT = "DOCUMENT"
    SECTION = "SECTION"
    SLIDE = "SLIDE"
    SUBHEADING = "SUBHEADING"
    PARAGRAPH = "PARAGRAPH"
    LIST = "LIST"
    LIST_ITEM = "LIST_ITEM"
    IMAGE = "IMAGE"
    TABLE = "TABLE"
    CAPTION = "CAPTION"


@dataclass
class Node:
    id: str
    type: str
    title: str = ""
    text: str = ""
    bbox: Optional[List[float]] = None
    page: Optional[int] = None
    children: List["Node"] = field(default_factory=list)
    level: int = 1
    ordered: bool = False
    html: Optional[str] = None
    image_path: Optional[str] = None

    def add_child(self, child: "Node") -> "Node":
        self.children.append(child)
        return child
