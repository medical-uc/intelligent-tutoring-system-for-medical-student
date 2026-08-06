import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union
import cv2
import numpy as np
import torch
from .preprocessing import BoundingBox


@dataclass
class LabelGuidePointResult:
    """
    Container for the final processed label result containing numeric ID, recognized text,
    original bounding box, and guide point coordinates mapped to original full image resolution.
    """
    label_id: int
    text: str
    bbox: BoundingBox
    guide_point_orig: Tuple[float, float]
    detected_points_orig: List[List[float]] = field(default_factory=list)
    guide_point_roi: Tuple[float, float] = (0.0, 0.0)
    detected_points_roi: List[List[float]] = field(default_factory=list)
    confidence_scores: List[float] = field(default_factory=list)


def extract_keypoints_from_heatmap(
    heatmap_probs: Union[torch.Tensor, np.ndarray],
    top_k: Optional[int] = 2,
    threshold: Optional[float] = 0.5,
    nms_radius: int = 5
) -> List[List[float]]:
    """
    Extract keypoints from predicted probability heatmap using Non-Maximum Suppression (NMS).

    Args:
        heatmap_probs: 2D array or Tensor of predicted probability heatmap.
        top_k: Maximum number of peak keypoints to return (default 2).
        threshold: Minimum probability threshold for a valid peak (default 0.5).
        nms_radius: Radius for morphological dilation in local maximum suppression.

    Returns:
        List of [x_coord, y_coord, confidence_score] ordered by confidence score descending.
    """
    if isinstance(heatmap_probs, torch.Tensor):
        heatmap_probs = heatmap_probs.squeeze().cpu().numpy()

    kernel_size = 2 * nms_radius + 1
    dilated = cv2.dilate(heatmap_probs, np.ones((kernel_size, kernel_size), np.uint8))
    local_max = (heatmap_probs == dilated)
    if threshold is not None:
        local_max = local_max & (heatmap_probs >= threshold)
    
    y_coords, x_coords = np.where(local_max)
    scores = heatmap_probs[y_coords, x_coords]
    
    order = np.argsort(-scores)
    if top_k is not None:
        order = order[:top_k]

    points = []
    for idx in order:
        points.append([float(x_coords[idx]), float(y_coords[idx]), float(scores[idx])])
        
    return points


def compute_weighted_guide_point(
    points: List[List[float]],
    bbox_center: Tuple[float, float],
    single_pred_weight: float = 0.75,
    max_weight_diff_ratio: float = 0.25,
    pred_total_weight: float = 0.75
) -> Tuple[float, float]:
    """
    Computes the guide point position for numeric label placement based on detected leader line keypoints:
    
    1. If 0 points detected: Returns the center of the text bounding box.
    2. If 1 point detected: Computes weighted average between predicted point and bbox center,
       giving higher weight to the predicted point so the label is next to (not directly on top of) the point.
    3. If 2 points detected: Computes weighted average of the 2 predicted points based on heatmap confidence scores
       (where maximum weight difference between the two points is capped at 0.2 out of 1.0, i.e., 2/10),
       and then blends the combined point with the bbox center.

    Args:
        points: List of detected peak points [[x, y, score], ...] ordered by score descending.
        bbox_center: Tuple (cx, cy) of text bounding box center.
        single_pred_weight: Weight assigned to predicted point when 1 point is detected (default 0.75).
        max_weight_diff_ratio: Maximum relative weight difference between 2 points (default 0.2).
        pred_total_weight: Total weight assigned to combined predicted points vs bbox center when 2 points detected.

    Returns:
        Tuple (x_guide, y_guide) of computed guide point.
    """
    cx, cy = float(bbox_center[0]), float(bbox_center[1])

    if len(points) == 0:
        return (cx, cy)

    if len(points) == 1:
        px, py = float(points[0][0]), float(points[0][1])
        gx = single_pred_weight * px + (1.0 - single_pred_weight) * cx
        gy = single_pred_weight * py + (1.0 - single_pred_weight) * cy
        return (gx, gy)

    # 2 or more points detected
    p1_x, p1_y, s1 = float(points[0][0]), float(points[0][1]), float(points[0][2])
    p2_x, p2_y, s2 = float(points[1][0]), float(points[1][1]), float(points[1][2])

    if s1 + s2 > 0:
        raw_w1 = s1 / (s1 + s2)
        raw_w2 = s2 / (s1 + s2)
    else:
        raw_w1, raw_w2 = 0.5, 0.5

    w_diff = abs(raw_w1 - raw_w2)
    if w_diff > max_weight_diff_ratio:
        if raw_w1 > raw_w2:
            norm_w1 = 0.5 + (max_weight_diff_ratio / 2.0)
            norm_w2 = 0.5 - (max_weight_diff_ratio / 2.0)
        else:
            norm_w1 = 0.5 - (max_weight_diff_ratio / 2.0)
            norm_w2 = 0.5 + (max_weight_diff_ratio / 2.0)
    else:
        norm_w1, norm_w2 = raw_w1, raw_w2

    comb_x = norm_w1 * p1_x + norm_w2 * p2_x
    comb_y = norm_w1 * p1_y + norm_w2 * p2_y

    gx = pred_total_weight * comb_x + (1.0 - pred_total_weight) * cx
    gy = pred_total_weight * comb_y + (1.0 - pred_total_weight) * cy
    return (gx, gy)


def export_label_mapping_json(
    results: List[LabelGuidePointResult],
    save_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Exports structured label mapping metadata dict mapping numeric label IDs to recognized text
    and guide point coordinates.

    Returns:
        Dict structure containing mapped label entries.
    """
    label_map = []
    for r in results:
        label_map.append({
            "id": r.label_id,
            "text": r.text,
            "bbox": [r.bbox.x1, r.bbox.y1, r.bbox.x2, r.bbox.y2],
            "guide_point": [r.guide_point_orig[0], r.guide_point_orig[1]],
            "detected_points": r.detected_points_orig,
            "confidence_scores": r.confidence_scores
        })

    output_data = {
        "num_labels": len(results),
        "labels": label_map
    }

    if save_path:
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2)

    return output_data


def compute_localization_metrics(
    predicted_points: List[List[float]],
    ground_truth_points: List[List[float]],
    distance_threshold: float = 10.0
) -> Dict[str, float]:
    """
    Computes Precision, Recall, F1, and mean Euclidean distance error for predicted keypoints vs ground truth.

    Args:
        predicted_points: List of predicted keypoints [[x, y, score], ...].
        ground_truth_points: List of ground truth keypoints [[x, y], ...].
        distance_threshold: Maximum distance (in pixels) to consider a predicted keypoint a True Positive.

    Returns:
        Dict containing precision, recall, f1, and mean_distance.
    """
    if not ground_truth_points:
        return {"precision": 1.0 if not predicted_points else 0.0, "recall": 1.0, "f1": 1.0, "mean_distance": 0.0}

    if not predicted_points:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0, "mean_distance": float("inf")}

    gt_matched = [False] * len(ground_truth_points)
    pred_matched = [False] * len(predicted_points)
    distances = []

    for p_idx, p in enumerate(predicted_points):
        px, py = float(p[0]), float(p[1])
        best_dist = float("inf")
        best_gt_idx = -1

        for g_idx, g in enumerate(ground_truth_points):
            if gt_matched[g_idx]:
                continue
            gx, gy = float(g[0]), float(g[1])
            dist = np.sqrt((px - gx)**2 + (py - gy)**2)
            if dist < best_dist:
                best_dist = dist
                best_gt_idx = g_idx

        if best_gt_idx != -1 and best_dist <= distance_threshold:
            gt_matched[best_gt_idx] = True
            pred_matched[p_idx] = True
            distances.append(best_dist)

    tp = sum(pred_matched)
    fp = len(predicted_points) - tp
    fn = len(ground_truth_points) - sum(gt_matched)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
    mean_dist = float(np.mean(distances)) if distances else 0.0

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "mean_distance": mean_dist
    }
