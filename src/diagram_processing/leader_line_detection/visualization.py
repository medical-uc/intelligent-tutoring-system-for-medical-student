from typing import List, Optional, Tuple
import cv2
import matplotlib.pyplot as plt
import numpy as np
from .postprocessing import LabelGuidePointResult


def render_prediction_overlay(
    image: np.ndarray,
    predicted_heatmap: np.ndarray,
    predicted_points: List[List[float]],
    ground_truth_points: Optional[List[List[float]]] = None,
    mask: Optional[np.ndarray] = None,
    save_path: Optional[str] = None
) -> plt.Figure:
    """
    Renders a 3-panel visualization figure for an inference prediction:
    1) Input RGB Image with ROI mask overlay.
    2) Predicted Heatmap probabilities.
    3) Overlay displaying predicted (red cross) and ground truth (green circle) endpoints.

    Args:
        image: RGB image numpy array (H, W, 3).
        predicted_heatmap: Predicted probability heatmap (H, W).
        predicted_points: List of [x, y, confidence_score].
        ground_truth_points: Optional list of [x, y].
        mask: Optional binary mask (H, W).
        save_path: Optional output filepath to save figure PNG.

    Returns:
        matplotlib Figure object.
    """
    fig, axes = plt.subplots(1, 3, figsize=(12, 4))

    img_disp = image.copy()
    if img_disp.dtype == np.uint8:
        img_disp = img_disp.astype(np.float32) / 255.0

    # Panel 0: Input Image + Mask
    axes[0].imshow(img_disp)
    if mask is not None:
        axes[0].imshow(mask, alpha=0.3, cmap="Oranges")
    axes[0].set_title("Input Image & Mask")
    axes[0].axis("off")

    # Panel 1: Predicted Heatmap
    axes[1].imshow(predicted_heatmap, cmap="jet", vmin=0.0, vmax=1.0)
    axes[1].set_title(f"Heatmap (Peak: {predicted_heatmap.max():.2f})")
    axes[1].axis("off")

    # Panel 2: Keypoints Overlay
    axes[2].imshow(img_disp)
    if ground_truth_points:
        for pt in ground_truth_points:
            if len(pt) >= 2:
                axes[2].plot(pt[0], pt[1], "go", markersize=8, markeredgecolor="white", markeredgewidth=1.5)
    for pt in predicted_points:
        axes[2].plot(pt[0], pt[1], "rx", markersize=10, markeredgewidth=2)

    gt_count = len(ground_truth_points) if ground_truth_points else 0
    axes[2].set_title(f"Overlay ({len(predicted_points)} Pred, {gt_count} GT)")
    axes[2].axis("off")

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=200, bbox_inches="tight")
    return fig


def render_numbered_diagram(
    image: np.ndarray,
    results: List[LabelGuidePointResult],
    mask_text: bool = True,
    mask_method: str = "solid",
    expand_px: float = 4.0,
    draw_guide_link: bool = False,
    draw_keypoints: bool = False,
    badge_radius: int = 14,
    save_path: Optional[str] = None
) -> Tuple[np.ndarray, plt.Figure]:
    """
    Renders the renumbered diagram image:
    1. Erases/inpaints original OCR text bounding boxes (using solid fill, cv2.inpaint, or Gaussian blur).
    2. Draws numeric callout badges (1, 2, 3...) at the computed weighted guide point positions.
    3. Optionally draws guide link lines and raw keypoints if debug mode is active.

    Args:
        image: Original RGB full diagram image (H, W, 3).
        results: List of LabelGuidePointResult.
        mask_text: If True, inpaints or masks original text bounding boxes.
        mask_method: Method for label removal ('solid', 'inpaint', 'blur'). Default is 'solid'.
        expand_px: Padding pixels to expand bounding box for masking/inpainting.
        draw_guide_link: If True, draws connecting line from text box center to guide point.
        draw_keypoints: If True, draws detected endpoint keypoint crosses.
        badge_radius: Pixel radius for numeric badge circles.
        save_path: Optional output path to save visualization PNG.

    Returns:
        Tuple of (rendered_rgb_image_array, matplotlib_figure).
    """
    canvas = image.copy()
    img_h, img_w = canvas.shape[:2]

    # 1. Mask original OCR text regions
    if mask_text:
        if mask_method == "inpaint":
            inpaint_mask = np.zeros((img_h, img_w), dtype=np.uint8)
            for res in results:
                b = res.bbox
                mx1 = max(0, int(b.x1 - expand_px))
                my1 = max(0, int(b.y1 - expand_px))
                mx2 = min(img_w, int(b.x2 + expand_px))
                my2 = min(img_h, int(b.y2 + expand_px))
                cv2.rectangle(inpaint_mask, (mx1, my1), (mx2, my2), 255, -1)

            canvas_bgr = cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR)
            canvas_bgr = cv2.inpaint(canvas_bgr, inpaint_mask, inpaintRadius=3, flags=cv2.INPAINT_TELEA)
            canvas = cv2.cvtColor(canvas_bgr, cv2.COLOR_BGR2RGB)

        elif mask_method == "blur":
            for res in results:
                b = res.bbox
                mx1 = max(0, int(b.x1 - expand_px))
                my1 = max(0, int(b.y1 - expand_px))
                mx2 = min(img_w, int(b.x2 + expand_px))
                my2 = min(img_h, int(b.y2 + expand_px))

                roi = canvas[my1:my2, mx1:mx2]
                if roi.size > 0:
                    blurred_roi = cv2.GaussianBlur(roi, (21, 21), 0)
                    canvas[my1:my2, mx1:mx2] = blurred_roi

        else:  # 'solid' method
            for res in results:
                b = res.bbox
                mx1 = max(0, int(b.x1 - expand_px))
                my1 = max(0, int(b.y1 - expand_px))
                mx2 = min(img_w, int(b.x2 + expand_px))
                my2 = min(img_h, int(b.y2 + expand_px))

                roi = canvas[my1:my2, mx1:mx2]
                if roi.size > 0:
                    bg_color = np.mean(roi, axis=(0, 1)).astype(np.uint8)
                    cv2.rectangle(canvas, (mx1, my1), (mx2, my2), bg_color.tolist(), -1)

    # Matplotlib Figure Rendering
    fig, ax = plt.subplots(figsize=(10, 10 * (img_h / img_w)))
    ax.imshow(canvas)

    for res in results:
        label_id = res.label_id
        gx, gy = res.guide_point_orig
        cx, cy = res.bbox.center.x, res.bbox.center.y

        # Draw connecting guide link line if debug is True
        if draw_guide_link:
            ax.plot([cx, gx], [cy, gy], color="yellow", linestyle="--", linewidth=1.5, alpha=0.8)

        # Draw raw detected keypoints if debug is True
        if draw_keypoints and res.detected_points_orig:
            for pt in res.detected_points_orig:
                ax.plot(pt[0], pt[1], "rx", markersize=8, markeredgewidth=2)

        # Draw numeric callout badge (white circle with black outline and bold text)
        circle_fill = plt.Circle((gx, gy), badge_radius, color="white", ec="black", lw=1.5, zorder=5)
        ax.add_patch(circle_fill)
        ax.text(
            gx, gy, str(label_id),
            color="black", fontweight="bold", fontsize=11,
            ha="center", va="center", zorder=6
        )

    ax.axis("off")
    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=200, bbox_inches="tight")

    return canvas, fig


def render_label_legend(
    results: List[LabelGuidePointResult],
    save_path: Optional[str] = None
) -> plt.Figure:
    """
    Renders a formatted text table/card displaying numeric IDs alongside recognized text labels.

    Args:
        results: List of LabelGuidePointResult.
        save_path: Optional output path to save legend PNG.

    Returns:
        matplotlib Figure object.
    """
    n = len(results)
    if n == 0:
        fig, ax = plt.subplots(figsize=(6, 2))
        ax.text(0.5, 0.5, "No Labels Detected", ha="center", va="center", fontsize=14)
        ax.axis("off")
        if save_path:
            plt.savefig(save_path, dpi=200, bbox_inches="tight")
        return fig

    rows = (n + 1) // 2
    fig_h = max(2.5, rows * 0.45 + 1.0)
    fig, ax = plt.subplots(figsize=(8, fig_h))

    ax.text(0.5, 0.95, "Diagram Label Legend", ha="center", va="top", fontsize=14, fontweight="bold")

    left_results = results[:rows]
    right_results = results[rows:]

    # Col 1 (Left Half)
    y_start = 0.85
    y_step = 0.75 / max(rows, 1)

    for i, res in enumerate(left_results):
        y_pos = y_start - i * y_step
        ax.text(0.05, y_pos, f"{res.label_id}.", fontweight="bold", fontsize=11, ha="left", va="center")
        ax.text(0.12, y_pos, res.text, fontsize=10, ha="left", va="center")

    # Col 2 (Right Half)
    for i, res in enumerate(right_results):
        y_pos = y_start - i * y_step
        ax.text(0.55, y_pos, f"{res.label_id}.", fontweight="bold", fontsize=11, ha="left", va="center")
        ax.text(0.62, y_pos, res.text, fontsize=10, ha="left", va="center")

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=200, bbox_inches="tight")

    return fig
