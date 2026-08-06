"""
Leader Line Endpoint Detection Package (5-Channel Input Architecture)
"""

from .model import UNetLeaderLineDetector, DoubleConv
from .preprocessing import (
    BoundingBox,
    OCRTextRegion,
    ROICropMetadata,
    compute_target_proximity_field,
    compute_neighbor_mask,
    extract_roi_and_metadata,
    preprocess_5channel_input,
    preprocess_image_and_mask,
    reverse_roi_points_to_original,
)
from .postprocessing import (
    LabelGuidePointResult,
    compute_localization_metrics,
    compute_weighted_guide_point,
    export_label_mapping_json,
    extract_keypoints_from_heatmap,
)
from .detector import LeaderLineEndpointDetector
from .visualization import (
    render_label_legend,
    render_numbered_diagram,
    render_prediction_overlay,
)
from .merging import (
    is_continuation_line,
    should_merge_horizontal,
    should_merge_vertical,
    merge_two_regions,
    merge_ocr_regions,
    merge_ocr_labels,
)

__all__ = [
    "UNetLeaderLineDetector",
    "DoubleConv",
    "BoundingBox",
    "OCRTextRegion",
    "ROICropMetadata",
    "LabelGuidePointResult",
    "compute_target_proximity_field",
    "compute_neighbor_mask",
    "preprocess_5channel_input",
    "preprocess_image_and_mask",
    "extract_roi_and_metadata",
    "reverse_roi_points_to_original",
    "extract_keypoints_from_heatmap",
    "compute_weighted_guide_point",
    "export_label_mapping_json",
    "compute_localization_metrics",
    "LeaderLineEndpointDetector",
    "render_prediction_overlay",
    "render_numbered_diagram",
    "render_label_legend",
    "is_continuation_line",
    "should_merge_horizontal",
    "should_merge_vertical",
    "merge_two_regions",
    "merge_ocr_regions",
    "merge_ocr_labels",
]
