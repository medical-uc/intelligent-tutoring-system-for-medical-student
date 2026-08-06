import os
import re
import json
import time
import traceback
from pathlib import Path
from typing import List, Dict, Any, Optional

import cv2
import numpy as np
import matplotlib.pyplot as plt

from .classifier.medgemma_classifier import MedGemmaClassifier
from .leader_line_detection import (
    LeaderLineEndpointDetector,
    OCRTextRegion,
    BoundingBox,
    merge_ocr_regions,
    render_numbered_diagram,
    render_label_legend,
    render_prediction_overlay,
    export_label_mapping_json,
    extract_roi_and_metadata,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHECKPOINT = str(PROJECT_ROOT / "checkpoints" / "leader_line_detection.pth")
DEFAULT_VLM_MODEL = "mlx-community/medgemma-4b-it-4bit"


def sanitize_filename(text: str) -> str:
    """Sanitizes text strings for safe filename usage."""
    s = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "_", s)[:40] or "label"


def run_paddle_ocr(ocr_engine, image_bgr: np.ndarray) -> List[OCRTextRegion]:
    """
    Runs PaddleOCR on full diagram image crop to detect text bounding boxes and recognized labels.
    """
    ocr_regions = []
    try:
        results = ocr_engine.predict(image_bgr)
        if not results:
            return ocr_regions

        res = results[0]
        dt_polys = res.get("dt_polys", []) if isinstance(res, dict) else getattr(res, "dt_polys", [])
        rec_texts = res.get("rec_texts", []) if isinstance(res, dict) else getattr(res, "rec_texts", [])
        rec_scores = res.get("rec_scores", []) if isinstance(res, dict) else getattr(res, "rec_scores", [])

        for idx, poly in enumerate(dt_polys):
            text = rec_texts[idx] if idx < len(rec_texts) else f"Label {idx + 1}"
            conf = float(rec_scores[idx]) if idx < len(rec_scores) else 1.0

            xs = [pt[0] for pt in poly]
            ys = [pt[1] for pt in poly]
            x1, y1, x2, y2 = float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))

            ocr_regions.append(
                OCRTextRegion(
                    text=text.strip() or f"Label {idx + 1}",
                    bbox=BoundingBox(x1, y1, x2, y2),
                    confidence=conf,
                    id=idx + 1,
                    poly=[(float(pt[0]), float(pt[1])) for pt in poly]
                )
            )
    except Exception as e:
        print(f"[Warning] PaddleOCR extraction encountered error: {e}")

    return ocr_regions


class DiagramPipelineRunner:
    """
    Unified Pipeline Runner for Diagram VLM Classification (MedGemma 4B)
    and Leader Line Endpoint Detection.
    """
    def __init__(
        self,
        checkpoint_path: Optional[str] = None,
        vlm_model_path: str = DEFAULT_VLM_MODEL,
        device: Optional[str] = None
    ):
        self.checkpoint_path = checkpoint_path or DEFAULT_CHECKPOINT
        self.vlm_model_path = vlm_model_path

        # Lazy initialized modules
        self.classifier = None
        self.keypoint_detector = None
        self.ocr_engine = None

    def _init_classifier(self):
        if self.classifier is None:
            self.classifier = MedGemmaClassifier(model_path=self.vlm_model_path)

    def _init_keypoint_detector(self):
        if self.keypoint_detector is None:
            print(f"Initializing Leader Line Endpoint Detector with checkpoint '{self.checkpoint_path}'...")
            self.keypoint_detector = LeaderLineEndpointDetector(
                checkpoint_path=self.checkpoint_path,
                image_size=256
            )

    def _init_ocr(self):
        if self.ocr_engine is None:
            print("Initializing PaddleOCR for text label region detection...")
            os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
            from paddleocr import PaddleOCR
            self.ocr_engine = PaddleOCR(
                lang="en",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False
            )

    def process_image_crop(
        self,
        image_path: str,
        output_dir: str,
        threshold: float = 0.5,
        top_k: int = 2,
        mask_method: str = "solid",
        debug: bool = False
    ) -> Dict[str, Any]:
        """
        Processes a single extracted visual image crop during PDF ingestion:
        1. MedGemma 4B VLM classification check ("labeled diagram" vs "not labeled diagram").
        2. If "labeled diagram": PaddleOCR label detection + Leader Line keypoint detection.
        3. Saves renumbered diagram PNG, label mappings JSON, and legend/heatmaps if debug is enabled.

        Returns:
            Dict summary containing classification, label mapping data, and generated file paths.
        """
        self._init_classifier()
        cls_result = self.classifier.classify_image(image_path)
        category = cls_result["category"]

        result = {
            "image_path": image_path,
            "category": category,
            "is_labeled_diagram": (category == "labeled diagram"),
            "raw_output": cls_result.get("raw_output", ""),
            "label_mapping": [],
            "num_labels": 0,
            "output_files": {}
        }

        if category != "labeled diagram":
            return result

        # Perform leader line detection & label extraction
        try:
            self._init_keypoint_detector()
            self._init_ocr()

            img_bgr = cv2.imread(image_path)
            if img_bgr is None:
                raise ValueError(f"Could not read image at '{image_path}'")
            img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

            os.makedirs(output_dir, exist_ok=True)

            raw_ocr_regions = run_paddle_ocr(self.ocr_engine, img_bgr)
            ocr_regions = merge_ocr_regions(raw_ocr_regions)

            if ocr_regions:
                label_results = self.keypoint_detector.detect_image_labels(
                    image=img_rgb,
                    ocr_regions=ocr_regions,
                    threshold=threshold,
                    top_k=top_k
                )

                renumbered_file = os.path.join(output_dir, "renumbered_diagram.png")
                mapping_json_file = os.path.join(output_dir, "label_mapping.json")

                _, fig_diagram = render_numbered_diagram(
                    image=img_rgb,
                    results=label_results,
                    mask_text=True,
                    mask_method=mask_method,
                    draw_guide_link=debug,
                    draw_keypoints=debug,
                    save_path=renumbered_file
                )
                plt.close(fig_diagram)

                mapping_data = export_label_mapping_json(results=label_results, save_path=mapping_json_file)

                result["label_mapping"] = mapping_data.get("labels", [])
                result["num_labels"] = len(label_results)
                result["output_files"]["renumbered_diagram"] = renumbered_file
                result["output_files"]["label_mapping_json"] = mapping_json_file

                if debug:
                    legend_file = os.path.join(output_dir, "label_legend.png")
                    fig_legend = render_label_legend(results=label_results, save_path=legend_file)
                    plt.close(fig_legend)
                    result["output_files"]["label_legend"] = legend_file

        except Exception as e:
            print(f"[Warning] Diagram processing error on '{image_path}': {e}")
            traceback.print_exc()

        return result
