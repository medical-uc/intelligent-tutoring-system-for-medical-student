from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Union
import cv2
import numpy as np
import torch


class BoundingBox:
    """
    Representation of a 2D bounding box with coordinates (x1, y1, x2, y2).
    """
    def __init__(self, x1: float, y1: float, x2: float, y2: float):
        self.x1 = float(x1)
        self.y1 = float(y1)
        self.x2 = float(x2)
        self.y2 = float(y2)

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    @property
    def center(self):
        class Point:
            def __init__(self, x: float, y: float):
                self.x = x
                self.y = y
        return Point((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    def clip(self, max_width: float, max_height: float) -> "BoundingBox":
        """Clips bounding box coordinates to image dimensions [0, max_width] and [0, max_height]."""
        return BoundingBox(
            x1=max(0.0, min(self.x1, max_width)),
            y1=max(0.0, min(self.y1, max_height)),
            x2=max(0.0, min(self.x2, max_width)),
            y2=max(0.0, min(self.y2, max_height))
        )


@dataclass
class OCRTextRegion:
    """
    Container for recognized OCR text string alongside its bounding box and metadata.
    """
    text: str
    bbox: BoundingBox
    confidence: float = 1.0
    id: Optional[int] = None
    poly: Optional[List[Tuple[float, float]]] = field(default=None)


@dataclass
class ROICropMetadata:
    """
    Metadata recording coordinate transformations between full image and resized ROI space.
    """
    crop_x1: int
    crop_y1: int
    crop_x2: int
    crop_y2: int
    pad_left: int
    pad_top: int
    pad_right: int
    pad_bottom: int
    scale_x: float
    scale_y: float
    orig_shape: Tuple[int, int]  # (height, width) of original image


def compute_target_proximity_field(
    shape: Tuple[int, int],
    bbox: Tuple[float, float, float, float],
    sigma: float = 20.0
) -> np.ndarray:
    """
    Computes Channel 4: Continuous Gaussian spatial decay field centered on target OCR text box boundary.

    Args:
        shape: (height, width) of ROI crop (e.g. (256, 256)).
        bbox: Target bounding box (x1, y1, x2, y2) in ROI crop coordinates.
        sigma: Gaussian decay standard deviation parameter.

    Returns:
        2D numpy array of shape (height, width) with values in range [0.0, 1.0].
    """
    h, w = shape
    x1, y1, x2, y2 = bbox
    y_grid, x_grid = np.ogrid[:h, :w]
    dx = np.maximum(0.0, np.maximum(x1 - x_grid, x_grid - x2))
    dy = np.maximum(0.0, np.maximum(y1 - y_grid, y_grid - y2))
    dist = np.sqrt(dx**2 + dy**2)
    proximity = np.exp(-(dist**2) / (2.0 * (sigma**2)))
    return proximity.astype(np.float32)


def compute_neighbor_mask(
    shape: Tuple[int, int],
    neighbor_bboxes: List[Union[BoundingBox, List[float], Tuple[float, float, float, float]]]
) -> np.ndarray:
    """
    Computes Channel 5: Binary mask marking all non-target OCR bounding boxes present inside the ROI crop.

    Args:
        shape: (height, width) of ROI crop (e.g. (256, 256)).
        neighbor_bboxes: List of neighbor bounding boxes in ROI crop coordinates.

    Returns:
        2D numpy array of shape (height, width) with values 1.0 inside neighbor bboxes, 0.0 elsewhere.
    """
    mask = np.zeros(shape, dtype=np.float32)
    for nb in neighbor_bboxes:
        if isinstance(nb, BoundingBox):
            nx1, ny1, nx2, ny2 = int(round(nb.x1)), int(round(nb.y1)), int(round(nb.x2)), int(round(nb.y2))
        else:
            nx1, ny1, nx2, ny2 = int(round(nb[0])), int(round(nb[1])), int(round(nb[2])), int(round(nb[3]))

        if nx2 > 0 and nx1 < shape[1] and ny2 > 0 and ny1 < shape[0]:
            cv2.rectangle(
                mask,
                (max(0, nx1), max(0, ny1)),
                (min(shape[1], nx2), min(shape[0], ny2)),
                1.0,
                -1
            )
    return mask


def extract_roi_and_metadata(
    image: np.ndarray,
    bbox: Union[BoundingBox, List[float], Tuple[float, float, float, float]],
    neighbor_bboxes: Optional[List[Union[BoundingBox, List[float], Tuple[float, float, float, float]]]] = None,
    margin: int = 65,
    min_margin: int = 65,
    target_size: int = 256,
    proximity_sigma: float = 20.0
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, ROICropMetadata]:
    """
    Extracts 5-channel ROI crop components centered on a target text bounding box:
    - Channels 1-3: Target text masked with mean background color.
    - Channel 4: Target proximity Gaussian spatial decay field.
    - Channel 5: Neighbor labels binary mask.

    Returns:
        Tuple of (masked_roi_256, target_prox_256, neighbor_mask_256, metadata)
    """
    img_h, img_w = image.shape[:2]

    if isinstance(bbox, BoundingBox):
        x1, y1, x2, y2 = bbox.x1, bbox.y1, bbox.x2, bbox.y2
    else:
        x1, y1, x2, y2 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])

    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    bbox_w = x2 - x1
    bbox_h = y2 - y1

    eff_margin = max(min_margin, margin)
    bbox_max = max(bbox_w, bbox_h)
    bbox_with_margin = int(round(bbox_max + 2 * eff_margin))

    if bbox_with_margin > target_size:
        crop_size = bbox_with_margin
    else:
        crop_size = target_size

    half_s = crop_size / 2.0

    # Horizontal Clamping
    if img_w >= crop_size:
        crop_x1 = int(round(cx - half_s))
        crop_x1 = max(0, min(img_w - crop_size, crop_x1))
        crop_x2 = crop_x1 + crop_size
        pad_left, pad_right = 0, 0
    else:
        crop_x1, crop_x2 = 0, img_w
        pad_left = (crop_size - img_w) // 2
        pad_right = crop_size - img_w - pad_left

    # Vertical Clamping
    if img_h >= crop_size:
        crop_y1 = int(round(cy - half_s))
        crop_y1 = max(0, min(img_h - crop_size, crop_y1))
        crop_y2 = crop_y1 + crop_size
        pad_top, pad_bottom = 0, 0
    else:
        crop_y1, crop_y2 = 0, img_h
        pad_top = (crop_size - img_h) // 2
        pad_bottom = crop_size - img_h - pad_top

    # Extract crop
    img_crop = image[crop_y1:crop_y2, crop_x1:crop_x2]
    if pad_left > 0 or pad_top > 0 or pad_right > 0 or pad_bottom > 0:
        img_crop = cv2.copyMakeBorder(
            img_crop, pad_top, pad_bottom, pad_left, pad_right,
            cv2.BORDER_REPLICATE
        )

    crop_h_actual, crop_w_actual = img_crop.shape[:2]
    scale_x = target_size / float(crop_w_actual)
    scale_y = target_size / float(crop_h_actual)

    # Resize crop to target size (256x256)
    img_crop_256 = cv2.resize(img_crop, (target_size, target_size), interpolation=cv2.INTER_AREA)

    # Local target bbox in 256x256 ROI space
    local_x1 = int(round((x1 - crop_x1 + pad_left) * scale_x))
    local_y1 = int(round((y1 - crop_y1 + pad_top) * scale_y))
    local_x2 = int(round((x2 - crop_x1 + pad_left) * scale_x))
    local_y2 = int(round((y2 - crop_y1 + pad_top) * scale_y))
    target_bbox_local = (local_x1, local_y1, local_x2, local_y2)

    # Channels 1-3: Mask target text region using mean background color
    masked_roi = img_crop_256.copy()
    bg_color = np.mean(img_crop_256, axis=(0, 1)).astype(np.uint8)
    pad_mask = 4
    mask_x1 = max(0, local_x1 - pad_mask)
    mask_y1 = max(0, local_y1 - pad_mask)
    mask_x2 = min(target_size, local_x2 + pad_mask)
    mask_y2 = min(target_size, local_y2 + pad_mask)
    cv2.rectangle(masked_roi, (mask_x1, mask_y1), (mask_x2, mask_y2), bg_color.tolist(), -1)

    # Channel 4: Target Proximity Field
    target_prox = compute_target_proximity_field((target_size, target_size), target_bbox_local, sigma=proximity_sigma)

    # Channel 5: Neighbor Labels Mask
    neighbor_bboxes_local = []
    if neighbor_bboxes:
        for nb in neighbor_bboxes:
            if isinstance(nb, BoundingBox):
                nx1_o, ny1_o, nx2_o, ny2_o = nb.x1, nb.y1, nb.x2, nb.y2
            else:
                nx1_o, ny1_o, nx2_o, ny2_o = float(nb[0]), float(nb[1]), float(nb[2]), float(nb[3])

            nx1 = int(round((nx1_o - crop_x1 + pad_left) * scale_x))
            ny1 = int(round((ny1_o - crop_y1 + pad_top) * scale_y))
            nx2 = int(round((nx2_o - crop_x1 + pad_left) * scale_x))
            ny2 = int(round((ny2_o - crop_y1 + pad_top) * scale_y))
            neighbor_bboxes_local.append((nx1, ny1, nx2, ny2))

    neighbor_mask = compute_neighbor_mask((target_size, target_size), neighbor_bboxes_local)

    metadata = ROICropMetadata(
        crop_x1=crop_x1,
        crop_y1=crop_y1,
        crop_x2=crop_x2,
        crop_y2=crop_y2,
        pad_left=pad_left,
        pad_top=pad_top,
        pad_right=pad_right,
        pad_bottom=pad_bottom,
        scale_x=scale_x,
        scale_y=scale_y,
        orig_shape=(img_h, img_w)
    )

    return masked_roi, target_prox, neighbor_mask, metadata


def reverse_roi_points_to_original(
    points: List[List[float]],
    metadata: ROICropMetadata
) -> List[List[float]]:
    """
    Inverse coordinate transformation: maps points from 256x256 ROI space
    back to original full diagram image resolution.

    Args:
        points: List of [x_roi, y_roi] or [x_roi, y_roi, score, ...] in ROI space.
        metadata: ROICropMetadata recorded during ROI extraction.

    Returns:
        List of [x_orig, y_orig, ...] mapped to original image coordinates.
    """
    mapped_points = []
    for pt in points:
        rx, ry = float(pt[0]), float(pt[1])
        x_orig = (rx / metadata.scale_x) - metadata.pad_left + metadata.crop_x1
        y_orig = (ry / metadata.scale_y) - metadata.pad_top + metadata.crop_y1

        # Preserve any additional values (e.g. confidence score)
        new_pt = [float(x_orig), float(y_orig)] + [float(val) for val in pt[2:]]
        mapped_points.append(new_pt)

    return mapped_points


def preprocess_5channel_input(
    image: np.ndarray,
    target_prox: Optional[np.ndarray] = None,
    neighbor_mask: Optional[np.ndarray] = None,
    image_size: int = 256
) -> torch.Tensor:
    """
    Preprocesses masked RGB image (H, W, 3), target proximity field (H, W), and neighbor mask (H, W)
    into a 5-channel PyTorch tensor of shape (1, 5, image_size, image_size).
    Applies ImageNet normalization to RGB channels (1-3).
    """
    if image.shape[-1] != 3:
        raise ValueError(f"Expected image with 3 channels (H, W, 3), got shape {image.shape}")

    if image.shape[0] != image_size or image.shape[1] != image_size:
        img_resized = cv2.resize(image, (image_size, image_size), interpolation=cv2.INTER_AREA)
    else:
        img_resized = image

    img_float = img_resized.astype(np.float32) / 255.0

    # ImageNet normalization parameters
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    img_norm = (img_float - mean) / std

    # Target proximity field (Channel 4)
    if target_prox is None:
        target_prox = np.zeros((image_size, image_size), dtype=np.float32)
    elif target_prox.shape != (image_size, image_size):
        target_prox = cv2.resize(target_prox.astype(np.float32), (image_size, image_size), interpolation=cv2.INTER_AREA)

    # Neighbor mask (Channel 5)
    if neighbor_mask is None:
        neighbor_mask = np.zeros((image_size, image_size), dtype=np.float32)
    elif neighbor_mask.shape != (image_size, image_size):
        neighbor_mask = cv2.resize(neighbor_mask.astype(np.float32), (image_size, image_size), interpolation=cv2.INTER_AREA)

    img_tensor = torch.from_numpy(img_norm).permute(2, 0, 1)                      # (3, H, W)
    target_prox_tensor = torch.from_numpy(target_prox[np.newaxis, :, :].astype(np.float32))     # (1, H, W)
    neighbor_mask_tensor = torch.from_numpy(neighbor_mask[np.newaxis, :, :].astype(np.float32)) # (1, H, W)

    input_tensor = torch.cat([img_tensor, target_prox_tensor, neighbor_mask_tensor], dim=0) # (5, H, W)
    return input_tensor.unsqueeze(0)


preprocess_image_and_mask = preprocess_5channel_input
