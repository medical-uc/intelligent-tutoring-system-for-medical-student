"""Routes MinerU2.5-tagged visual content to the right captioning model.

Clinical photos and microscopy (type == "image") go to LLaVA-Med, which
was trained on PMC figure-caption pairs and produces visually grounded
biomedical captions. Structured visuals (type in {"table", "chart"}, and
diagrams) go to Qwen2.5-VL, which handles layout, labels, and tabular data
better than LLaVA-Med's 7B backbone.
"""

from .llava_med import LlavaMedCaptioner
from .qwen_vl import QwenVLCaptioner

STRUCTURED_TYPES = {"table", "chart", "diagram"}
PHOTO_TYPES = {"image"}


class VisualCaptionRouter:
    def __init__(self):
        self._llava_med = None
        self._qwen_vl = None

    def _get_llava_med(self) -> LlavaMedCaptioner:
        if self._llava_med is None:
            self._llava_med = LlavaMedCaptioner()
        return self._llava_med

    def _get_qwen_vl(self) -> QwenVLCaptioner:
        if self._qwen_vl is None:
            self._qwen_vl = QwenVLCaptioner()
        return self._qwen_vl

    def route(self, content_type: str) -> str:
        """Returns 'llava_med' or 'qwen_vl' for a MinerU2.5 content type."""
        if content_type in STRUCTURED_TYPES:
            return "qwen_vl"
        if content_type in PHOTO_TYPES:
            return "llava_med"
        raise ValueError(f"Unroutable content type: {content_type!r}")

    def caption(self, image_path: str, content_type: str) -> dict:
        model = self.route(content_type)

        if model == "llava_med":
            text = self._get_llava_med().caption(image_path)
        else:
            qwen = self._get_qwen_vl()
            text = qwen.caption(image_path)
            qwen.unload()  # respect load-one-at-a-time memory budget

        return {"image_path": image_path, "content_type": content_type, "model": model, "caption": text}
