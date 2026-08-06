import os
from pathlib import Path
from typing import List, Optional, Tuple, Union
import numpy as np
import torch

from .merging import merge_ocr_regions
from .model import UNetLeaderLineDetector
from .postprocessing import (
    LabelGuidePointResult,
    compute_weighted_guide_point,
    extract_keypoints_from_heatmap,
)
from .preprocessing import (
    OCRTextRegion,
    extract_roi_and_metadata,
    preprocess_5channel_input,
    reverse_roi_points_to_original,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CHECKPOINT_PATH = str(PROJECT_ROOT / "checkpoints" / "leader_line_detection.pth")


class LeaderLineEndpointDetector:
    """
    High-level inference engine for detecting leader line endpoints in 5-channel ROI inputs
    and processing full diagram images with multi-label guide point placement.
    """
    def __init__(
        self,
        checkpoint_path: Optional[str] = None,
        device: Optional[Union[str, torch.device]] = None,
        image_size: int = 256
    ):
        if device is None:
            if torch.cuda.is_available():
                self.device = torch.device("cuda")
            elif torch.backends.mps.is_available():
                self.device = torch.device("mps")
            else:
                self.device = torch.device("cpu")
        else:
            self.device = torch.device(device)

        self.image_size = image_size
        self.model = UNetLeaderLineDetector(in_channels=5, out_channels=1).to(self.device)

        if checkpoint_path is None:
            checkpoint_path = DEFAULT_CHECKPOINT_PATH

        if checkpoint_path and os.path.exists(checkpoint_path):
            checkpoint = torch.load(checkpoint_path, map_location=self.device)
            if isinstance(checkpoint, dict) and "model_state_dict" in checkpoint:
                self.model.load_state_dict(checkpoint["model_state_dict"])
            elif isinstance(checkpoint, dict):
                self.model.load_state_dict(checkpoint)
            else:
                self.model = checkpoint
        else:
            print(f"[Warning] Leader line detector checkpoint not found at '{checkpoint_path}'. Model initialized with raw weights.")

        self.model.eval()

    def detect(
        self,
        image: np.ndarray,
        target_prox: Optional[np.ndarray] = None,
        neighbor_mask: Optional[np.ndarray] = None,
        top_k: Optional[int] = 2,
        threshold: Optional[float] = 0.5,
        nms_radius: int = 5
    ) -> Tuple[List[List[float]], np.ndarray]:
        """
        Detect leader line endpoints on a single input ROI image using 5-channel architecture.

        Args:
            image: Input masked RGB image array of shape (H, W, 3) (target text masked).
            target_prox: Channel 4 continuous Gaussian target proximity field (H, W).
            neighbor_mask: Channel 5 binary neighbor labels mask (H, W).
            top_k: Top K peaks to extract (default 2).
            threshold: Minimum probability threshold for peak detection (default 0.5).
            nms_radius: Radius for non-maximum suppression.

        Returns:
            Tuple of (predicted_keypoints, predicted_heatmap)
            - predicted_keypoints: List of [x, y, confidence_score]
            - predicted_heatmap: 2D numpy array of predicted probabilities (256, 256)
        """
        input_tensor = preprocess_5channel_input(
            image,
            target_prox=target_prox,
            neighbor_mask=neighbor_mask,
            image_size=self.image_size
        ).to(self.device)

        with torch.no_grad():
            probs = self.model(input_tensor).squeeze().cpu().numpy()

        points = extract_keypoints_from_heatmap(probs, top_k=top_k, threshold=threshold, nms_radius=nms_radius)
        return points, probs

    def detect_image_labels(
        self,
        image: np.ndarray,
        ocr_regions: List[OCRTextRegion],
        top_k: Optional[int] = 2,
        threshold: Optional[float] = 0.5,
        nms_radius: int = 5
    ) -> List[LabelGuidePointResult]:
        """
        Processes a full diagram image alongside recognized OCR text regions.
        Extracts 5-channel ROI crops for each region, runs leader line detection,
        and computes weighted guide points mapped back to full image resolution.

        Args:
            image: Full RGB diagram image numpy array (H, W, 3).
            ocr_regions: List of merged OCRTextRegion objects.
            top_k: Top K peak keypoints per label ROI.
            threshold: Probability threshold for endpoint peaks.
            nms_radius: Radius for NMS peak extraction.

        Returns:
            List of LabelGuidePointResult instances.
        """
        results = []
        if not ocr_regions:
            return results

        for idx, target_region in enumerate(ocr_regions):
            label_id = target_region.id if target_region.id is not None else (idx + 1)
            neighbor_bboxes = [reg.bbox for reg in ocr_regions if reg is not target_region]

            masked_roi, target_prox, neighbor_mask, metadata = extract_roi_and_metadata(
                image=image,
                bbox=target_region.bbox,
                neighbor_bboxes=neighbor_bboxes,
                target_size=self.image_size
            )

            pts_roi, _ = self.detect(
                image=masked_roi,
                target_prox=target_prox,
                neighbor_mask=neighbor_mask,
                top_k=top_k,
                threshold=threshold,
                nms_radius=nms_radius
            )

            pts_orig = reverse_roi_points_to_original(pts_roi, metadata)

            bbox_center = (target_region.bbox.center.x, target_region.bbox.center.y)
            guide_orig = compute_weighted_guide_point(pts_orig, bbox_center=bbox_center)
            guide_roi = compute_weighted_guide_point(pts_roi, bbox_center=(128.0, 128.0))

            scores = [p[2] for p in pts_roi if len(p) >= 3]

            results.append(
                LabelGuidePointResult(
                    label_id=label_id,
                    text=target_region.text,
                    bbox=target_region.bbox,
                    guide_point_orig=guide_orig,
                    detected_points_orig=pts_orig,
                    guide_point_roi=guide_roi,
                    detected_points_roi=pts_roi,
                    confidence_scores=scores
                )
            )

        return results
