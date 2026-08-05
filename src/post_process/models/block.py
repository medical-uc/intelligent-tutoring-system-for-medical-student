from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .feature import Feature


@dataclass
class Block:
    id: str
    page: int
    bbox: List[float]  # [x0, y0, x1, y1]
    text: str
    raw_type: str
    spans: List[Dict[str, Any]] = field(default_factory=list)
    features: Optional[Feature] = None
    semantic_type: Optional[str] = None
    confidence: float = 1.0
    html: Optional[str] = None
    image_path: Optional[str] = None
