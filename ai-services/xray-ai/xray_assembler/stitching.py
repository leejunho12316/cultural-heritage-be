from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Any, Iterable

import cv2
import numpy as np

from .config import AssemblyConfig
from .image_ops import ensure_output_channels, normalize_preview, principal_angle_deg
from .models import Fragment, Placement, SearchGeometry


def _normalize_angle(angle_deg: float) -> float:
    return ((float(angle_deg) + 180.0) % 360.0) - 180.0


def _translation(tx: float, ty: float) -> np.ndarray:
    return np.array([[1.0, 0.0, tx], [0.0, 1.0, ty], [0.0, 0.0, 1.0]], dtype=np.float64)


def _rotation_about(angle_deg: float, cx: float, cy: float) -> np.ndarray:
    theta = math.radians(float(angle_deg))
    c, s = math.cos(theta), math.sin(theta)
    rotation = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64)
    return _translation(cx, cy) @ rotation @ _translation(-cx, -cy)


def _rotation_bound_matrix(width: int, height: int, angle_deg: float) -> tuple[np.ndarray, tuple[int, int]]:
    center = ((width - 1) / 2.0, (height - 1) / 2.0)
    matrix = cv2.getRotationMatrix2D(center, float(angle_deg), 1.0).astype(np.float64)
    cos_v, sin_v = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_w = max(1, int(math.ceil(height * sin_v + width * cos_v)))
    new_h = max(1, int(math.ceil(height * cos_v + width * sin_v)))
    matrix[0, 2] += (new_w - 1) / 2.0 - center[0]
    matrix[1, 2] += (new_h - 1) / 2.0 - center[1]
    return np.vstack([matrix, [0.0, 0.0, 1.0]]), (new_w, new_h)


def _transform_points(matrix: np.ndarray, points_xy: np.ndarray) -> np.ndarray:
    points = np.asarray(points_xy, dtype=np.float64)
    homogeneous = np.column_stack([points, np.ones(len(points), dtype=np.float64)])
    transformed = (matrix @ homogeneous.T).T
    return transformed[:, :2]


def _fragment_corners(fragment: Fragment) -> np.ndarray:
    height, width = fragment.mask.shape
    return np.array([[0.0, 0.0], [width, 0.0], [width, height], [0.0, height]], dtype=np.float64)


def _resize_fragment_for_analysis(fragment: Fragment, max_dimension: int) -> tuple[np.ndarray, np.ndarray, float]:
    gray = fragment.gray
    mask = fragment.mask
    height, width = mask.shape
    scale = min(1.0, float(max_dimension) / max(height, width)) if max_dimension > 0 else 1.0
    if scale >= 0.999:
        return gray.copy(), mask.copy(), 1.0
    size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    gray_small = cv2.resize(gray, size, interpolation=cv2.INTER_AREA)
    mask_small = cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST)
    return gray_small, mask_small, scale


def _resize_pair_for_analysis(
    first: Fragment, second: Fragment, max_dimension: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float]:
    first_h, first_w = first.mask.shape
    second_h, second_w = second.mask.shape
    maximum = max(first_h, first_w, second_h, second_w)
    scale = min(1.0, float(max_dimension) / maximum) if max_dimension > 0 else 1.0
    if scale >= 0.999:
        return first.gray.copy(), first.mask.copy(), second.gray.copy(), second.mask.copy(), 1.0
    first_size = (max(1, int(round(first_w * scale))), max(1, int(round(first_h * scale))))
    second_size = (max(1, int(round(second_w * scale))), max(1, int(round(second_h * scale))))
    return (
        cv2.resize(first.gray, first_size, interpolation=cv2.INTER_AREA),
        cv2.resize(first.mask, first_size, interpolation=cv2.INTER_NEAREST),
        cv2.resize(second.gray, second_size, interpolation=cv2.INTER_AREA),
        cv2.resize(second.mask, second_size, interpolation=cv2.INTER_NEAREST),
        scale,
    )


def _normalize_gray(gray: np.ndarray, mask: np.ndarray) -> np.ndarray:
    gray8 = gray if gray.dtype == np.uint8 else cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    values = gray8[mask > 0]
    if values.size < 16:
        return gray8
    low, high = np.percentile(values, [1.0, 99.0])
    if high <= low:
        return gray8
    normalized = np.clip((gray8.astype(np.float32) - float(low)) * 255.0 / float(high - low), 0, 255).astype(np.uint8)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    return clahe.apply(normalized)


def _phase_signal(gray: np.ndarray, mask: np.ndarray) -> np.ndarray:
    normalized = _normalize_gray(gray, mask).astype(np.float32) / 255.0
    grad_x = cv2.Sobel(normalized, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(normalized, cv2.CV_32F, 0, 1, ksize=3)
    gradient = cv2.magnitude(grad_x, grad_y)
    gradient *= (mask > 0).astype(np.float32)
    edge = cv2.morphologyEx(
        (mask > 0).astype(np.uint8),
        cv2.MORPH_GRADIENT,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
    ).astype(np.float32)
    return (cv2.GaussianBlur(gradient, (0, 0), 1.0) + 0.45 * edge).astype(np.float32)


def _pad_center(array: np.ndarray, side: int) -> tuple[np.ndarray, tuple[int, int]]:
    height, width = array.shape[:2]
    canvas = np.zeros((side, side), dtype=array.dtype)
    x0, y0 = (side - width) // 2, (side - height) // 2
    canvas[y0 : y0 + height, x0 : x0 + width] = array
    return canvas, (x0, y0)


def _safe_ncc(first: np.ndarray, second: np.ndarray) -> float:
    if first.size < 32 or second.size != first.size:
        return -1.0
    a = first.astype(np.float32)
    b = second.astype(np.float32)
    a -= float(a.mean())
    b -= float(b.mean())
    denominator = float(a.std() * b.std())
    if denominator < 1e-6:
        return 0.0
    return float(np.mean(a * b) / denominator)


@dataclass
class PairRegistration:
    first_index: int
    second_index: int
    second_to_first: np.ndarray
    score: float
    overlap_ratio: float
    intensity_ncc: float
    gradient_ncc: float
    method: str
    inlier_count: int = 0
    match_count: int = 0
    phase_response: float = 0.0
    boundary_ncc: float = 0.0
    boundary_gradient_ncc: float = 0.0
    boundary_occupancy_ncc: float = 0.0
    boundary_reliability: float = 0.0
    direction: str = ""
    selection_confidence: float = 0.0
    review_required: bool = False
    selection_reason: str = ""
    cycle_path_length: int = 0
    cycle_translation_residual_px: float = 0.0
    cycle_rotation_residual_deg: float = 0.0
    cycle_consistent: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "firstIndex": self.first_index,
            "secondIndex": self.second_index,
            "secondToFirst": self.second_to_first.tolist(),
            "score": float(self.score),
            "overlapRatio": float(self.overlap_ratio),
            "intensityNcc": float(self.intensity_ncc),
            "gradientNcc": float(self.gradient_ncc),
            "method": self.method,
            "inlierCount": int(self.inlier_count),
            "matchCount": int(self.match_count),
            "phaseResponse": float(self.phase_response),
            "boundaryNcc": float(self.boundary_ncc),
            "boundaryGradientNcc": float(self.boundary_gradient_ncc),
            "boundaryOccupancyNcc": float(self.boundary_occupancy_ncc),
            "boundaryReliability": float(self.boundary_reliability),
            "direction": self.direction,
            "selectionConfidence": float(self.selection_confidence),
            "reviewRequired": bool(self.review_required),
            "selectionReason": self.selection_reason,
            "cyclePathLength": int(self.cycle_path_length),
            "cycleTranslationResidualPx": float(self.cycle_translation_residual_px),
            "cycleRotationResidualDeg": float(self.cycle_rotation_residual_deg),
            "cycleConsistent": self.cycle_consistent,
        }


def _pair_metrics(
    first_gray: np.ndarray,
    first_mask: np.ndarray,
    second_gray: np.ndarray,
    second_mask: np.ndarray,
    second_to_first: np.ndarray,
) -> tuple[float, float, float]:
    first_h, first_w = first_mask.shape
    second_h, second_w = second_mask.shape
    first_corners = np.array([[0, 0], [first_w, 0], [first_w, first_h], [0, first_h]], dtype=np.float64)
    second_corners = _transform_points(
        second_to_first,
        np.array([[0, 0], [second_w, 0], [second_w, second_h], [0, second_h]], dtype=np.float64),
    )
    all_points = np.vstack([first_corners, second_corners])
    minimum = np.floor(all_points.min(axis=0) - 3.0)
    maximum = np.ceil(all_points.max(axis=0) + 3.0)
    canvas_w, canvas_h = np.maximum((maximum - minimum).astype(int), 1)
    offset = _translation(-minimum[0], -minimum[1])

    first_warp = offset
    second_warp = offset @ second_to_first
    first_mask_canvas = cv2.warpAffine(
        first_mask,
        first_warp[:2].astype(np.float32),
        (int(canvas_w), int(canvas_h)),
        flags=cv2.INTER_NEAREST,
    )
    second_mask_canvas = cv2.warpAffine(
        second_mask,
        second_warp[:2].astype(np.float32),
        (int(canvas_w), int(canvas_h)),
        flags=cv2.INTER_NEAREST,
    )
    overlap = (first_mask_canvas > 0) & (second_mask_canvas > 0)
    intersection = int(np.count_nonzero(overlap))
    minimum_area = min(int(np.count_nonzero(first_mask_canvas)), int(np.count_nonzero(second_mask_canvas)))
    overlap_ratio = intersection / max(minimum_area, 1)
    if intersection < 64:
        return float(overlap_ratio), -1.0, -1.0

    first_image_canvas = cv2.warpAffine(
        _normalize_gray(first_gray, first_mask),
        first_warp[:2].astype(np.float32),
        (int(canvas_w), int(canvas_h)),
        flags=cv2.INTER_LINEAR,
    )
    second_image_canvas = cv2.warpAffine(
        _normalize_gray(second_gray, second_mask),
        second_warp[:2].astype(np.float32),
        (int(canvas_w), int(canvas_h)),
        flags=cv2.INTER_LINEAR,
    )
    intensity_ncc = _safe_ncc(first_image_canvas[overlap], second_image_canvas[overlap])

    first_grad_x = cv2.Sobel(first_image_canvas.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
    first_grad_y = cv2.Sobel(first_image_canvas.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
    second_grad_x = cv2.Sobel(second_image_canvas.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
    second_grad_y = cv2.Sobel(second_image_canvas.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
    first_gradient = cv2.magnitude(first_grad_x, first_grad_y)
    second_gradient = cv2.magnitude(second_grad_x, second_grad_y)
    gradient_ncc = _safe_ncc(first_gradient[overlap], second_gradient[overlap])
    return float(overlap_ratio), float(intensity_ncc), float(gradient_ncc)


def _registration_score(overlap: float, intensity_ncc: float, gradient_ncc: float, support: float = 0.0) -> float:
    intensity_unit = float(np.clip((intensity_ncc + 1.0) * 0.5, 0.0, 1.0))
    gradient_unit = float(np.clip((gradient_ncc + 1.0) * 0.5, 0.0, 1.0))
    return float(0.38 * overlap + 0.36 * intensity_unit + 0.20 * gradient_unit + 0.06 * np.clip(support, 0.0, 1.0))


def _rigid_from_correspondences(source_xy: np.ndarray, destination_xy: np.ndarray) -> np.ndarray:
    source_center = source_xy.mean(axis=0)
    destination_center = destination_xy.mean(axis=0)
    source_zero = source_xy - source_center
    destination_zero = destination_xy - destination_center
    covariance = source_zero.T @ destination_zero
    u, _, vt = np.linalg.svd(covariance)
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0:
        vt[-1, :] *= -1
        rotation = vt.T @ u.T
    translation = destination_center - rotation @ source_center
    matrix = np.eye(3, dtype=np.float64)
    matrix[:2, :2] = rotation
    matrix[:2, 2] = translation
    return matrix


def _sift_candidate(
    first: Fragment,
    second: Fragment,
    config: AssemblyConfig,
) -> PairRegistration | None:
    if not hasattr(cv2, "SIFT_create"):
        return None
    first_gray, first_mask, second_gray, second_mask, pair_scale = _resize_pair_for_analysis(
        first, second, config.pair_analysis_max_dimension
    )
    detector = cv2.SIFT_create(
        nfeatures=int(config.sift_features),
        contrastThreshold=float(config.sift_contrast_threshold),
        edgeThreshold=20,
        sigma=1.2,
    )
    keypoints_first, descriptors_first = detector.detectAndCompute(_normalize_gray(first_gray, first_mask), first_mask)
    keypoints_second, descriptors_second = detector.detectAndCompute(_normalize_gray(second_gray, second_mask), second_mask)
    if descriptors_first is None or descriptors_second is None or len(keypoints_first) < 4 or len(keypoints_second) < 4:
        return None
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    pairs = matcher.knnMatch(descriptors_second, descriptors_first, k=2)
    good = [first_match for first_match, second_match in pairs if first_match.distance < config.sift_ratio_test * second_match.distance]
    if len(good) < 4:
        return None
    source = np.float32([keypoints_second[match.queryIdx].pt for match in good])
    destination = np.float32([keypoints_first[match.trainIdx].pt for match in good])
    affine, inlier_mask = cv2.estimateAffinePartial2D(
        source,
        destination,
        method=cv2.RANSAC,
        ransacReprojThreshold=float(config.sift_ransac_threshold_px),
        maxIters=10000,
        confidence=0.999,
        refineIters=20,
    )
    if affine is None or inlier_mask is None:
        return None
    inliers = inlier_mask.reshape(-1) > 0
    inlier_count = int(np.count_nonzero(inliers))
    if inlier_count < int(config.sift_min_inliers):
        return None
    linear = affine[:, :2]
    scale_estimate = math.sqrt(max(abs(float(np.linalg.det(linear))), 0.0))
    if not (config.sift_min_scale <= scale_estimate <= config.sift_max_scale):
        return None
    rigid_small = _rigid_from_correspondences(source[inliers], destination[inliers])
    scale_matrix = np.diag([pair_scale, pair_scale, 1.0])
    rigid_full = np.linalg.inv(scale_matrix) @ rigid_small @ scale_matrix
    overlap, intensity_ncc, gradient_ncc = _pair_metrics(
        first.gray, first.mask, second.gray, second.mask, rigid_full
    )
    support = inlier_count / max(len(good), 1)
    score = _registration_score(overlap, intensity_ncc, gradient_ncc, support)
    return PairRegistration(
        first.index,
        second.index,
        rigid_full,
        score,
        overlap,
        intensity_ncc,
        gradient_ncc,
        "sift_ransac_fixed_scale",
        inlier_count=inlier_count,
        match_count=len(good),
    )


def _phase_candidates(first: Fragment, second: Fragment, config: AssemblyConfig) -> list[PairRegistration]:
    first_gray, first_mask, second_gray, second_mask, pair_scale = _resize_pair_for_analysis(
        first, second, config.pair_analysis_max_dimension
    )
    first_signal = _phase_signal(first_gray, first_mask)
    second_signal = _phase_signal(second_gray, second_mask)
    side = int(math.ceil(max(math.hypot(*first_signal.shape), math.hypot(*second_signal.shape))))
    side = max(32, side)
    first_padded, first_offset = _pad_center(first_signal, side)
    second_padded, second_offset = _pad_center(second_signal, side)
    window = cv2.createHanningWindow((side, side), cv2.CV_32F)
    center = ((side - 1) / 2.0, (side - 1) / 2.0)

    base = _normalize_angle(first.principal_angle_deg - second.principal_angle_deg)
    seeds = [0.0, base, _normalize_angle(base + 180.0), 180.0]
    angles: set[float] = set()
    for seed in seeds:
        for offset in range(-config.phase_angle_radius_deg, config.phase_angle_radius_deg + 1, config.phase_angle_step_deg):
            angles.add(round(_normalize_angle(seed + offset), 6))
    if config.phase_full_rotation_step_deg > 0:
        for angle in range(-180, 180, int(config.phase_full_rotation_step_deg)):
            angles.add(float(angle))

    scale_matrix = np.diag([pair_scale, pair_scale, 1.0])
    pad_first = _translation(first_offset[0], first_offset[1])
    pad_second = _translation(second_offset[0], second_offset[1])
    candidates: list[PairRegistration] = []
    for angle in sorted(angles):
        rotation_cv = cv2.getRotationMatrix2D(center, float(angle), 1.0).astype(np.float64)
        rotated_second = cv2.warpAffine(
            second_padded,
            rotation_cv,
            (side, side),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        shift, response = cv2.phaseCorrelate(first_padded, rotated_second, window)
        rotation = np.vstack([rotation_cv, [0.0, 0.0, 1.0]])
        for sign in (-1.0, 1.0):
            transform_small = (
                np.linalg.inv(pad_first)
                @ _translation(sign * shift[0], sign * shift[1])
                @ rotation
                @ pad_second
            )
            transform_full = np.linalg.inv(scale_matrix) @ transform_small @ scale_matrix
            overlap, intensity_ncc, gradient_ncc = _pair_metrics(
                first.gray, first.mask, second.gray, second.mask, transform_full
            )
            score = _registration_score(overlap, intensity_ncc, gradient_ncc, max(float(response), 0.0))
            candidates.append(
                PairRegistration(
                    first.index,
                    second.index,
                    transform_full,
                    score,
                    overlap,
                    intensity_ncc,
                    gradient_ncc,
                    "phase_correlation_rotation_search",
                    phase_response=float(response),
                )
            )
    return candidates


def _source_shape(fragment: Fragment) -> tuple[int, int]:
    shape = fragment.diagnostics.get("source_shape")
    if isinstance(shape, (list, tuple)) and len(shape) >= 2:
        return int(shape[0]), int(shape[1])
    x, y, width, height = fragment.crop_bbox_xywh
    return int(max(y + height, fragment.mask.shape[0])), int(max(x + width, fragment.mask.shape[1]))


def _crop_to_source(fragment: Fragment) -> np.ndarray:
    x, y, _, _ = fragment.crop_bbox_xywh
    return _translation(float(x), float(y))


def _source_to_crop_pair_transform(
    first: Fragment,
    second: Fragment,
    second_source_to_first_source: np.ndarray,
) -> np.ndarray:
    return (
        np.linalg.inv(_crop_to_source(first))
        @ second_source_to_first_source
        @ _crop_to_source(second)
    )


def _crop_to_source_pair_transform(
    first: Fragment,
    second: Fragment,
    second_crop_to_first_crop: np.ndarray,
) -> np.ndarray:
    return (
        _crop_to_source(first)
        @ second_crop_to_first_crop
        @ np.linalg.inv(_crop_to_source(second))
    )


def _source_frame_gray_mask(fragment: Fragment) -> tuple[np.ndarray, np.ndarray]:
    source_h, source_w = _source_shape(fragment)
    gray = np.zeros((source_h, source_w), dtype=fragment.gray.dtype)
    mask = np.zeros((source_h, source_w), dtype=np.uint8)
    x, y, width, height = fragment.crop_bbox_xywh
    crop_h = min(int(height), fragment.gray.shape[0], max(0, source_h - int(y)))
    crop_w = min(int(width), fragment.gray.shape[1], max(0, source_w - int(x)))
    if crop_h > 0 and crop_w > 0:
        gray[int(y) : int(y) + crop_h, int(x) : int(x) + crop_w] = fragment.gray[:crop_h, :crop_w]
        mask[int(y) : int(y) + crop_h, int(x) : int(x) + crop_w] = fragment.mask[:crop_h, :crop_w]
    return gray, mask


def _boundary_profile(
    gray: np.ndarray,
    mask: np.ndarray,
    side: str,
    strip_width: int,
    frame_border: int,
) -> np.ndarray:
    height, width = gray.shape
    offset = max(0, int(frame_border))
    strip = max(4, min(int(strip_width), max(height, width)))
    normalized = _normalize_gray(gray, mask).astype(np.float32) / 255.0
    if side == "left":
        image_strip = normalized[:, offset : min(width, offset + strip)]
        mask_strip = mask[:, offset : min(width, offset + strip)]
        tangent_axis = 0
    elif side == "right":
        start = max(0, width - offset - strip)
        end = max(start + 1, width - offset)
        image_strip = normalized[:, start:end]
        mask_strip = mask[:, start:end]
        tangent_axis = 0
    elif side == "top":
        image_strip = normalized[offset : min(height, offset + strip), :]
        mask_strip = mask[offset : min(height, offset + strip), :]
        tangent_axis = 1
    else:
        start = max(0, height - offset - strip)
        end = max(start + 1, height - offset)
        image_strip = normalized[start:end, :]
        mask_strip = mask[start:end, :]
        tangent_axis = 1

    if image_strip.size == 0:
        return np.zeros((1, 4), dtype=np.float32)
    valid = (mask_strip > 0).astype(np.float32)
    reduce_axis = 1 if tangent_axis == 0 else 0
    counts = valid.sum(axis=reduce_axis)
    occupancy = valid.mean(axis=reduce_axis)
    weighted_mean = (image_strip * valid).sum(axis=reduce_axis) / np.maximum(counts, 1.0)

    grad_x = cv2.Sobel(image_strip.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(image_strip.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
    gradient = cv2.magnitude(grad_x, grad_y)
    weighted_gradient = (gradient * valid).sum(axis=reduce_axis) / np.maximum(counts, 1.0)
    mask_gradient = cv2.morphologyEx(
        (mask_strip > 0).astype(np.uint8),
        cv2.MORPH_GRADIENT,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
    ).astype(np.float32)
    edge_density = mask_gradient.mean(axis=reduce_axis)
    return np.column_stack([weighted_mean, weighted_gradient, occupancy, edge_density]).astype(np.float32)


def _profile_channel_ncc(first: np.ndarray, second: np.ndarray, minimum_variance: float) -> tuple[float, bool]:
    if first.size < 8 or second.size != first.size:
        return 0.0, False
    first = first.astype(np.float32)
    second = second.astype(np.float32)
    first_std = float(first.std())
    second_std = float(second.std())
    if first_std < minimum_variance or second_std < minimum_variance:
        return 0.0, False
    value = float(
        np.mean((first - float(first.mean())) * (second - float(second.mean())))
        / (first_std * second_std)
    )
    return float(np.clip(value, -1.0, 1.0)), True


def _best_boundary_profile_alignment(
    first_profile: np.ndarray,
    second_profile: np.ndarray,
    config: AssemblyConfig,
) -> tuple[float, float, float, float, int]:
    first_length = len(first_profile)
    second_length = len(second_profile)
    maximum_shift = int(round(max(first_length, second_length) * float(config.frame_boundary_max_shift_ratio)))
    minimum_overlap = int(round(min(first_length, second_length) * float(config.frame_boundary_min_tangent_overlap_ratio)))
    minimum_variance = float(config.frame_boundary_min_variance)
    best: tuple[float, float, float, float, int] = (-1.0, 0.0, 0.0, 0.0, 0)
    for shift in range(-maximum_shift, maximum_shift + 1):
        first_start = max(0, shift)
        second_start = max(0, -shift)
        length = min(first_length - first_start, second_length - second_start)
        if length < max(8, minimum_overlap):
            continue
        first_slice = first_profile[first_start : first_start + length]
        second_slice = second_profile[second_start : second_start + length]
        correlations: list[float] = []
        valid_channels = 0
        for channel in range(first_slice.shape[1]):
            value, valid = _profile_channel_ncc(
                first_slice[:, channel], second_slice[:, channel], minimum_variance
            )
            correlations.append(value)
            valid_channels += int(valid)
        reliability = valid_channels / max(first_slice.shape[1], 1)
        intensity_ncc, gradient_ncc, occupancy_ncc, edge_ncc = correlations
        combined = (
            0.42 * intensity_ncc
            + 0.30 * gradient_ncc
            + 0.18 * occupancy_ncc
            + 0.10 * edge_ncc
        )
        combined *= 0.45 + 0.55 * reliability
        if combined > best[0]:
            best = (
                float(combined),
                float(gradient_ncc),
                float(occupancy_ncc),
                float(reliability),
                int(shift),
            )
    return best


def _direction_from_side_pair(first_side: str, second_side: str) -> str:
    if first_side == "right" and second_side == "left":
        return "right"
    if first_side == "left" and second_side == "right":
        return "left"
    if first_side == "bottom" and second_side == "top":
        return "down"
    return "up"


def _legacy_frame_continuation_candidates(
    first: Fragment,
    second: Fragment,
    config: AssemblyConfig,
) -> list[PairRegistration]:
    """Compatibility path for short/simple acquisitions.

    Existing validated small datasets used the foreground bounding boxes and
    principal-axis alternatives.  Long scanner runs use the boundary-profile
    sequence logic below instead.
    """
    if second.index != first.index + 1:
        return []
    first_sides = first.diagnostics.get("touch_sides", {})
    second_sides = second.diagnostics.get("touch_sides", {})
    side_pairs: list[tuple[str, str]] = []
    for first_side, second_side in (
        ("right", "left"),
        ("left", "right"),
        ("bottom", "top"),
        ("top", "bottom"),
    ):
        if bool(first_sides.get(first_side)) and bool(second_sides.get(second_side)):
            side_pairs.append((first_side, second_side))
    if not side_pairs:
        return []

    first_points = cv2.findNonZero((first.mask > 0).astype(np.uint8))
    second_points = cv2.findNonZero((second.mask > 0).astype(np.uint8))
    if first_points is None or second_points is None:
        return []
    first_xy = first_points.reshape(-1, 2).astype(np.float64)
    second_xy = second_points.reshape(-1, 2).astype(np.float64)
    first_min, first_max = first_xy.min(axis=0), first_xy.max(axis=0)
    second_h, second_w = second.mask.shape
    base = _normalize_angle(first.principal_angle_deg - second.principal_angle_deg)
    angle_options = [base, _normalize_angle(base + 180.0)]
    desired_overlap = max(2.0, float(config.frame_continuation_overlap_px))
    candidates: list[PairRegistration] = []
    for angle in angle_options:
        rotation = _rotation_about(
            angle,
            (second_w - 1) / 2.0,
            (second_h - 1) / 2.0,
        )
        rotated_second = _transform_points(rotation, second_xy)
        second_min, second_max = rotated_second.min(axis=0), rotated_second.max(axis=0)
        for first_side, second_side in side_pairs:
            if first_side == "right" and second_side == "left":
                tx = first_max[0] - desired_overlap - second_min[0]
                ty = 0.5 * (first_min[1] + first_max[1]) - 0.5 * (
                    second_min[1] + second_max[1]
                )
            elif first_side == "left" and second_side == "right":
                tx = first_min[0] + desired_overlap - second_max[0]
                ty = 0.5 * (first_min[1] + first_max[1]) - 0.5 * (
                    second_min[1] + second_max[1]
                )
            elif first_side == "bottom" and second_side == "top":
                tx = 0.5 * (first_min[0] + first_max[0]) - 0.5 * (
                    second_min[0] + second_max[0]
                )
                ty = first_max[1] - desired_overlap - second_min[1]
            else:
                tx = 0.5 * (first_min[0] + first_max[0]) - 0.5 * (
                    second_min[0] + second_max[0]
                )
                ty = first_min[1] + desired_overlap - second_max[1]
            transform = _translation(float(tx), float(ty)) @ rotation
            overlap, intensity_ncc, gradient_ncc = _pair_metrics(
                first.gray,
                first.mask,
                second.gray,
                second.mask,
                transform,
            )
            overlap_preference = math.exp(-abs(float(overlap) - 0.12) / 0.18)
            score = (
                float(config.frame_continuation_score)
                + 0.05 * np.clip((intensity_ncc + 1.0) * 0.5, 0.0, 1.0)
                + 0.04 * np.clip((gradient_ncc + 1.0) * 0.5, 0.0, 1.0)
                + 0.04 * overlap_preference
            )
            candidates.append(
                PairRegistration(
                    first.index,
                    second.index,
                    transform,
                    float(score),
                    float(overlap),
                    float(intensity_ncc),
                    float(gradient_ncc),
                    f"legacy_frame_continuation_{first_side}_to_{second_side}",
                    direction=_direction_from_side_pair(first_side, second_side),
                    selection_confidence=float(np.clip(score, 0.0, 1.0)),
                    review_required=False,
                    selection_reason="legacy_short_sequence_continuation",
                )
            )
    return candidates


def _frame_continuation_candidates(
    first: Fragment,
    second: Fragment,
    config: AssemblyConfig,
) -> list[PairRegistration]:
    """Generate same-direction scanner-frame continuation candidates.

    The candidate translation is expressed in the original scanner-frame
    coordinate system, not in the cropped foreground bounding boxes.  Each
    cardinal direction is scored from the opposing boundary profiles.  A weak
    candidate never receives a large unconditional base score.
    """
    if second.index != first.index + 1:
        return []
    first_sides = first.diagnostics.get("touch_sides", {})
    second_sides = second.diagnostics.get("touch_sides", {})
    side_pairs: list[tuple[str, str]] = []
    for first_side, second_side in (
        ("right", "left"),
        ("left", "right"),
        ("bottom", "top"),
        ("top", "bottom"),
    ):
        if bool(first_sides.get(first_side)) and bool(second_sides.get(second_side)):
            side_pairs.append((first_side, second_side))
    if not side_pairs:
        # Foreground segmentation can miss the exact scanner side on nearly
        # frame-filling radiographs.  In a consecutive scanner sequence, keep
        # all four cardinal hypotheses and let boundary evidence plus sequence
        # consistency decide.  Short or isolated pairs are still rejected by
        # the forest confidence rules.
        if _is_scanner_fragment(first) and _is_scanner_fragment(second):
            side_pairs = [
                ("right", "left"),
                ("left", "right"),
                ("bottom", "top"),
                ("top", "bottom"),
            ]
        else:
            return []

    first_source_gray, first_source_mask = _source_frame_gray_mask(first)
    second_source_gray, second_source_mask = _source_frame_gray_mask(second)
    first_h, first_w = first_source_mask.shape
    second_h, second_w = second_source_mask.shape
    desired_overlap = max(2.0, float(config.frame_continuation_overlap_px))
    frame_border = max(0, int(config.fragment_frame_border_px))

    candidates: list[PairRegistration] = []
    for first_side, second_side in side_pairs:
        first_profile = _boundary_profile(
            first_source_gray,
            first_source_mask,
            first_side,
            int(config.frame_boundary_strip_px),
            frame_border,
        )
        second_profile = _boundary_profile(
            second_source_gray,
            second_source_mask,
            second_side,
            int(config.frame_boundary_strip_px),
            frame_border,
        )
        boundary_ncc, boundary_gradient_ncc, occupancy_ncc, reliability, tangent_shift = (
            _best_boundary_profile_alignment(first_profile, second_profile, config)
        )
        direction = _direction_from_side_pair(first_side, second_side)
        if direction == "right":
            source_transform = _translation(first_w - desired_overlap, float(tangent_shift))
        elif direction == "left":
            source_transform = _translation(-(second_w - desired_overlap), float(tangent_shift))
        elif direction == "down":
            source_transform = _translation(float(tangent_shift), first_h - desired_overlap)
        else:
            source_transform = _translation(float(tangent_shift), -(second_h - desired_overlap))

        transform = _source_to_crop_pair_transform(first, second, source_transform)
        overlap, intensity_ncc, gradient_ncc = _pair_metrics(
            first.gray,
            first.mask,
            second.gray,
            second.mask,
            transform,
        )
        overlap_preference = math.exp(-abs(float(overlap) - 0.08) / 0.20)
        boundary_unit = float(np.clip((boundary_ncc + 1.0) * 0.5, 0.0, 1.0))
        boundary_gradient_unit = float(np.clip((boundary_gradient_ncc + 1.0) * 0.5, 0.0, 1.0))
        occupancy_unit = float(np.clip((occupancy_ncc + 1.0) * 0.5, 0.0, 1.0))
        intensity_unit = float(np.clip((intensity_ncc + 1.0) * 0.5, 0.0, 1.0))
        gradient_unit = float(np.clip((gradient_ncc + 1.0) * 0.5, 0.0, 1.0))
        score = (
            float(config.frame_boundary_base_score)
            + 0.34 * boundary_unit
            + 0.20 * boundary_gradient_unit
            + 0.12 * occupancy_unit
            + 0.12 * intensity_unit
            + 0.08 * gradient_unit
            + 0.08 * overlap_preference
            + 0.06 * reliability
        )
        # Flat boundary profiles are inherently ambiguous; retain them as
        # sequence candidates but lower their standalone confidence.
        score *= 0.60 + 0.40 * float(reliability)
        candidates.append(
            PairRegistration(
                first.index,
                second.index,
                transform,
                float(np.clip(score, 0.0, 1.0)),
                float(overlap),
                float(intensity_ncc),
                float(gradient_ncc),
                f"frame_continuation_boundary_{first_side}_to_{second_side}",
                boundary_ncc=float(boundary_ncc),
                boundary_gradient_ncc=float(boundary_gradient_ncc),
                boundary_occupancy_ncc=float(occupancy_ncc),
                boundary_reliability=float(reliability),
                direction=direction,
                selection_confidence=float(np.clip(score, 0.0, 1.0)),
                review_required=bool(score < float(config.frame_sequence_low_confidence_threshold)),
                selection_reason="boundary_profile_candidate",
            )
        )
    return candidates

def _is_scanner_fragment(fragment: Fragment) -> bool:
    method = str(fragment.diagnostics.get("method", ""))
    return bool(fragment.diagnostics.get("touches_frame", False)) or method.startswith("frame_filled_")


def _scanner_series_key(fragment: Fragment) -> str:
    """Return the acquisition-series stem without its trailing frame number.

    File order is only auxiliary evidence, but an explicit stem change such as
    ``object-22`` to ``object(2)-1`` is strong evidence that the operator
    started a different scan run.  Weak boundary continuation must not bridge
    that boundary; a genuine SIFT/RANSAC match may still do so.
    """
    stem = fragment.path.stem.strip()
    match = re.match(r"^(.*?)-\s*(\d+)$", stem)
    if match is None:
        return stem.casefold()
    return re.sub(r"\s+", " ", match.group(1).strip()).casefold()


def _longest_scanner_run(fragments: list[Fragment]) -> int:
    """Return the longest consecutive run of frame-clipped scanner images.

    The sequence/pose-graph path is intended for long acquisition runs.  Short
    fragment sets keep the already validated legacy pairwise behaviour.
    """
    longest = 0
    current = 0
    for fragment in fragments:
        if _is_scanner_fragment(fragment):
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _registration_direction(first: Fragment, second: Fragment, edge: PairRegistration) -> str:
    if edge.direction:
        return edge.direction
    source_transform = _crop_to_source_pair_transform(first, second, edge.second_to_first)
    tx = float(source_transform[0, 2])
    ty = float(source_transform[1, 2])
    if abs(tx) >= 1.20 * abs(ty):
        return "right" if tx >= 0 else "left"
    if abs(ty) >= 1.20 * abs(tx):
        return "down" if ty >= 0 else "up"
    # Diagonal SIFT anchors are treated as row transitions according to their
    # dominant displacement, while preserving the exact rigid transform.
    if abs(ty) >= abs(tx):
        return "down" if ty >= 0 else "up"
    return "right" if tx >= 0 else "left"


def _is_strong_registration(edge: PairRegistration, config: AssemblyConfig) -> bool:
    if edge.method.startswith("sift"):
        ratio = edge.inlier_count / max(edge.match_count, 1)
        return (
            edge.inlier_count >= int(config.sift_min_inliers)
            and ratio >= 0.40
            and edge.overlap_ratio >= float(config.pair_min_overlap)
        )
    if edge.method.startswith("phase"):
        return (
            edge.score >= float(config.pair_min_score)
            and edge.overlap_ratio >= float(config.pair_min_overlap)
            and edge.intensity_ncc >= 0.55
            and edge.gradient_ncc >= 0.20
            and edge.phase_response >= 0.015
        )
    if edge.method.startswith("source_constellation_translation_consensus"):
        return (
            edge.inlier_count
            >= int(config.fragment_array_source_consensus_min_matches)
            and edge.overlap_ratio
            >= float(config.fragment_array_source_consensus_min_overlap)
            and (
                edge.intensity_ncc
                >= float(config.fragment_array_source_consensus_min_intensity_ncc)
                or edge.gradient_ncc
                >= float(config.fragment_array_source_consensus_min_gradient_ncc)
            )
        )
    if edge.method.startswith("source_frame_transform_propagation"):
        return (
            edge.inlier_count
            >= int(config.fragment_array_source_transform_min_verified_matches)
            and edge.overlap_ratio
            >= float(config.fragment_array_source_transform_min_overlap)
            and (
                edge.intensity_ncc
                >= float(config.fragment_array_source_transform_min_intensity_ncc)
                or edge.gradient_ncc
                >= float(config.fragment_array_source_transform_min_gradient_ncc)
            )
        )
    if edge.method.startswith("source_boundary_profile"):
        return (
            edge.inlier_count >= int(config.fragment_array_boundary_min_matches)
            and edge.score >= float(config.fragment_array_boundary_min_score)
            and edge.boundary_reliability
            >= float(config.fragment_array_boundary_min_reliability)
            and edge.boundary_ncc >= float(config.fragment_array_boundary_min_ncc)
            and edge.boundary_gradient_ncc
            >= float(config.fragment_array_boundary_min_gradient_ncc)
            and edge.boundary_occupancy_ncc
            >= float(config.fragment_array_boundary_min_occupancy_ncc)
        )
    return False


def _is_extended_sift_anchor(edge: PairRegistration, config: AssemblyConfig) -> bool:
    """Stricter acceptance for non-adjacent scanner-frame SIFT matches."""
    if not edge.method.startswith("sift"):
        return False
    ratio = edge.inlier_count / max(edge.match_count, 1)
    return (
        edge.inlier_count >= max(
            int(config.sift_min_inliers),
            int(config.frame_extended_sift_min_inliers),
        )
        and ratio >= float(config.frame_extended_sift_min_inlier_ratio)
        and edge.overlap_ratio >= float(config.pair_min_overlap)
        and edge.intensity_ncc >= float(config.frame_extended_sift_min_intensity_ncc)
        and edge.gradient_ncc >= float(config.frame_extended_sift_min_gradient_ncc)
    )


def _extended_sift_confidence(edge: PairRegistration) -> float:
    ratio = edge.inlier_count / max(edge.match_count, 1)
    intensity_unit = float(np.clip((edge.intensity_ncc + 1.0) * 0.5, 0.0, 1.0))
    gradient_unit = float(np.clip((edge.gradient_ncc + 1.0) * 0.5, 0.0, 1.0))
    return float(
        np.clip(
            0.35 * edge.score
            + 0.30 * ratio
            + 0.20 * intensity_unit
            + 0.15 * gradient_unit,
            0.0,
            1.0,
        )
    )


def _registration_candidates(
    first: Fragment,
    second: Fragment,
    config: AssemblyConfig,
    use_boundary_continuation: bool = False,
) -> list[PairRegistration]:
    candidates: list[PairRegistration] = []
    sift = _sift_candidate(first, second, config)
    if sift is not None:
        direction = _registration_direction(first, second, sift)
        support = sift.inlier_count / max(sift.match_count, 1)
        sift = replace(
            sift,
            direction=direction,
            selection_confidence=float(np.clip(max(sift.score, support), 0.0, 1.0)),
            review_required=False,
            selection_reason="image_supported_sift_anchor",
        )
        candidates.append(sift)
        if (
            (sift.score >= config.sift_early_accept_score or sift.intensity_ncc >= 0.60)
            and sift.overlap_ratio >= config.pair_min_overlap
        ):
            return candidates

    if use_boundary_continuation:
        candidates.extend(_frame_continuation_candidates(first, second, config))
    else:
        candidates.extend(_legacy_frame_continuation_candidates(first, second, config))
    phase = _phase_candidates(first, second, config)
    for edge in phase:
        direction = _registration_direction(first, second, edge)
        confidence = float(np.clip(edge.score, 0.0, 1.0))
        candidates.append(
            replace(
                edge,
                direction=direction,
                selection_confidence=confidence,
                review_required=not _is_strong_registration(edge, config),
                selection_reason=(
                    "image_supported_phase_anchor"
                    if _is_strong_registration(edge, config)
                    else "phase_candidate_unconfirmed"
                ),
            )
        )
    if not candidates:
        raise RuntimeError(f"파편 등록 후보가 없습니다: {first.name} / {second.name}")
    return candidates


def estimate_pair_registration(first: Fragment, second: Fragment, config: AssemblyConfig) -> PairRegistration:
    candidates = _registration_candidates(first, second, config)
    candidates.sort(
        key=lambda candidate: (
            int(_is_strong_registration(candidate, config)),
            candidate.selection_confidence,
            candidate.score,
        ),
        reverse=True,
    )
    return candidates[0]


def _candidate_pairs(fragment_count: int, config: AssemblyConfig) -> list[tuple[int, int]]:
    if fragment_count <= int(config.pair_all_limit):
        return [(first, second) for first in range(fragment_count) for second in range(first + 1, fragment_count)]
    pairs: set[tuple[int, int]] = set()
    window = max(1, int(config.pair_index_window))
    for first in range(fragment_count):
        for second in range(first + 1, min(fragment_count, first + window + 1)):
            pairs.add((first, second))
    return sorted(pairs)


def _opposite_direction(direction: str | None) -> str | None:
    return {
        "left": "right",
        "right": "left",
        "up": "down",
        "down": "up",
    }.get(direction or "")


def _is_horizontal(direction: str | None) -> bool:
    return direction in {"left", "right"}


def _is_vertical(direction: str | None) -> bool:
    return direction in {"up", "down"}


def _source_frame_corners_in_crop(fragment: Fragment) -> np.ndarray:
    source_h, source_w = _source_shape(fragment)
    x, y, _, _ = fragment.crop_bbox_xywh
    return np.array(
        [
            [-float(x), -float(y)],
            [float(source_w - x), -float(y)],
            [float(source_w - x), float(source_h - y)],
            [-float(x), float(source_h - y)],
        ],
        dtype=np.float64,
    )


def _frame_box(fragment: Fragment, pose: np.ndarray) -> tuple[float, float, float, float]:
    points = _transform_points(pose, _source_frame_corners_in_crop(fragment))
    minimum = points.min(axis=0)
    maximum = points.max(axis=0)
    return float(minimum[0]), float(minimum[1]), float(maximum[0]), float(maximum[1])


def _box_overlap_ratio(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> float:
    x0 = max(first[0], second[0])
    y0 = max(first[1], second[1])
    x1 = min(first[2], second[2])
    y1 = min(first[3], second[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    first_area = max(1.0, first[2] - first[0]) * max(1.0, first[3] - first[1])
    second_area = max(1.0, second[2] - second[0]) * max(1.0, second[3] - second[1])
    return float(intersection / max(min(first_area, second_area), 1.0))


@dataclass
class _SequenceState:
    score: float
    choices: list[PairRegistration]
    adjustments: list[float]
    current_pose: np.ndarray
    boxes: list[tuple[float, float, float, float]]
    last_direction: str | None
    last_run_direction: str | None
    last_cross_direction: str | None


def _direction_axis(direction: str | None) -> str | None:
    if _is_horizontal(direction):
        return "horizontal"
    if _is_vertical(direction):
        return "vertical"
    return None


def _sequence_transition_adjustment(
    run_axis: str,
    previous_direction: str | None,
    last_run_direction: str | None,
    last_cross_direction: str | None,
    direction: str,
    config: AssemblyConfig,
) -> float:
    """Score a row-major or column-major serpentine scanner transition.

    ``run_axis`` is the axis along which the scanner normally takes several
    consecutive frames.  The other axis is the one-cell row/column advance.
    Both horizontal and vertical acquisition runs are evaluated; this avoids
    baking a horizontal-only assumption into long flat radiograph sequences.
    """
    if previous_direction is None:
        return 0.0
    previous_axis = _direction_axis(previous_direction)
    current_axis = _direction_axis(direction)
    if previous_axis is None or current_axis is None:
        return 0.0

    adjustment = 0.0
    if current_axis == run_axis:
        if previous_axis == run_axis:
            if direction == previous_direction:
                adjustment += float(config.frame_sequence_same_direction_bonus)
            elif direction == _opposite_direction(previous_direction):
                adjustment -= float(config.frame_sequence_reverse_penalty)
        else:
            # After advancing one scanner row/column, the run direction should
            # reverse in a serpentine acquisition.
            if last_run_direction is not None and direction == _opposite_direction(last_run_direction):
                adjustment += float(config.frame_sequence_serpentine_bonus)
            elif last_run_direction is not None and direction == last_run_direction:
                adjustment -= 0.35 * float(config.frame_sequence_serpentine_bonus)
            else:
                adjustment += float(config.frame_sequence_turn_bonus)
    else:
        if previous_axis != run_axis:
            # More than one consecutive cross-axis move usually stacks frames
            # into a column/row by mistake.  Reuse the existing penalty value
            # without changing the public configuration schema.
            adjustment -= float(config.frame_sequence_repeated_vertical_penalty)
        else:
            adjustment += float(config.frame_sequence_turn_bonus)
            # Successive row/column advances normally continue in one direction
            # while the run direction alternates.
            if last_cross_direction is not None:
                if direction == last_cross_direction:
                    adjustment += float(config.frame_sequence_serpentine_bonus)
                elif direction == _opposite_direction(last_cross_direction):
                    adjustment -= 0.35 * float(config.frame_sequence_reverse_penalty)
    return adjustment


def _sequence_choices_for_pair(
    candidates: list[PairRegistration],
    config: AssemblyConfig,
) -> list[PairRegistration]:
    strong_sift = [edge for edge in candidates if edge.method.startswith("sift") and _is_strong_registration(edge, config)]
    if strong_sift:
        return sorted(strong_sift, key=lambda edge: edge.selection_confidence, reverse=True)[:1]
    frame = [edge for edge in candidates if edge.method.startswith("frame_continuation")]
    if frame:
        # Keep one candidate per cardinal direction.  This prevents nearly
        # duplicate candidates from consuming the beam width.
        best_by_direction: dict[str, PairRegistration] = {}
        for edge in frame:
            current = best_by_direction.get(edge.direction)
            if current is None or edge.score > current.score:
                best_by_direction[edge.direction] = edge
        return sorted(best_by_direction.values(), key=lambda edge: edge.score, reverse=True)
    strong_phase = [edge for edge in candidates if edge.method.startswith("phase") and _is_strong_registration(edge, config)]
    if strong_phase:
        return sorted(strong_phase, key=lambda edge: edge.selection_confidence, reverse=True)[:2]
    return sorted(candidates, key=lambda edge: edge.selection_confidence, reverse=True)[:1]


def _phase_step_ratio_candidate(
    first: Fragment,
    second: Fragment,
    edge: PairRegistration,
    direction: str,
    config: AssemblyConfig,
) -> float | None:
    if not edge.method.startswith("phase"):
        return None
    if edge.direction != direction:
        return None
    if edge.score < float(config.pair_fallback_min_score):
        return None
    if edge.phase_response < 0.03:
        return None
    source_transform = _crop_to_source_pair_transform(
        first, second, edge.second_to_first
    )
    angle = abs(
        _normalize_angle(
            math.degrees(
                math.atan2(source_transform[1, 0], source_transform[0, 0])
            )
        )
    )
    if angle > max(7.0, float(config.frame_same_direction_max_deg)):
        return None
    source_h, source_w = _source_shape(first)
    dx = float(source_transform[0, 2])
    dy = float(source_transform[1, 2])
    if _is_horizontal(direction):
        primary = abs(dx)
        cross = abs(dy)
        dimension = max(float(source_w), 1.0)
        cross_dimension = max(float(source_h), 1.0)
    else:
        primary = abs(dy)
        cross = abs(dx)
        dimension = max(float(source_h), 1.0)
        cross_dimension = max(float(source_w), 1.0)
    ratio = primary / dimension
    if ratio < 0.15 or ratio > 0.85:
        return None
    if cross / cross_dimension > 0.35:
        return None
    return float(ratio)


def _strong_step_ratio_candidate(
    first: Fragment,
    second: Fragment,
    edge: PairRegistration,
    direction: str,
    config: AssemblyConfig,
) -> float | None:
    if not _is_strong_registration(edge, config):
        return None
    source_transform = _crop_to_source_pair_transform(
        first, second, edge.second_to_first
    )
    angle = abs(
        _normalize_angle(
            math.degrees(
                math.atan2(source_transform[1, 0], source_transform[0, 0])
            )
        )
    )
    if angle > max(7.0, float(config.frame_same_direction_max_deg)):
        return None
    source_h, source_w = _source_shape(first)
    dx = float(source_transform[0, 2])
    dy = float(source_transform[1, 2])
    if _is_horizontal(direction):
        primary = abs(dx)
        cross = abs(dy)
        dimension = max(float(source_w), 1.0)
        cross_dimension = max(float(source_h), 1.0)
    else:
        primary = abs(dy)
        cross = abs(dx)
        dimension = max(float(source_h), 1.0)
        cross_dimension = max(float(source_w), 1.0)
    ratio = primary / dimension
    if ratio < 0.15 or ratio > 0.85:
        return None
    if cross / cross_dimension > 0.35:
        return None
    return float(ratio)


def _calibrate_weak_sequence_steps(
    pair_indices: list[int],
    choices: list[PairRegistration],
    candidate_sets: dict[tuple[int, int], list[PairRegistration]],
    fragments: list[Fragment],
    config: AssemblyConfig,
) -> tuple[list[PairRegistration], dict[str, Any]]:
    """Compact a weak, feature-poor scanner grid using data-derived steps.

    Boundary continuation identifies direction well but its legacy translation
    assumes only a 24-pixel overlap, which can leave large gaps for heavily
    overlapping scanner tiles.  When fewer than 25% of the chain edges are
    image-supported, estimate horizontal/vertical step ratios from reliable
    phase/SIFT motions and blend them with a neutral half-frame fallback.
    Strong registrations are never modified.
    """
    if not choices:
        return choices, {"applied": False, "reason": "empty_sequence"}
    strong_count = sum(1 for edge in choices if _is_strong_registration(edge, config))
    strong_ratio = strong_count / max(len(choices), 1)
    if strong_ratio >= 0.25:
        return choices, {
            "applied": False,
            "reason": "sufficient_image_supported_edges",
            "strongEdgeCount": strong_count,
            "edgeCount": len(choices),
            "strongEdgeRatio": float(strong_ratio),
        }

    evidence: dict[str, list[float]] = {"horizontal": [], "vertical": []}
    for first_index, selected in zip(pair_indices, choices):
        second_index = first_index + 1
        direction = _registration_direction(
            fragments[first_index], fragments[second_index], selected
        )
        axis = _direction_axis(direction)
        if axis is None:
            continue
        strong_ratio_candidate = _strong_step_ratio_candidate(
            fragments[first_index],
            fragments[second_index],
            selected,
            direction,
            config,
        )
        if strong_ratio_candidate is not None:
            evidence[axis].append(strong_ratio_candidate)
            continue
        phase_ratios = [
            ratio
            for candidate in candidate_sets[(first_index, second_index)]
            if (
                ratio := _phase_step_ratio_candidate(
                    fragments[first_index],
                    fragments[second_index],
                    candidate,
                    direction,
                    config,
                )
            )
            is not None
        ]
        if phase_ratios:
            evidence[axis].append(float(np.mean(phase_ratios[:3])))

    default_ratio = 0.50
    step_ratios: dict[str, float] = {}
    for axis in ("horizontal", "vertical"):
        values = evidence[axis]
        if values:
            observed = float(np.mean(values))
            ratio = 0.5 * default_ratio + 0.5 * observed
        else:
            observed = default_ratio
            ratio = default_ratio
        step_ratios[axis] = float(np.clip(ratio, 0.35, 0.70))

    calibrated: list[PairRegistration] = []
    changed_pairs: list[list[int]] = []
    for first_index, edge in zip(pair_indices, choices):
        second_index = first_index + 1
        if not edge.method.startswith("frame_continuation"):
            calibrated.append(edge)
            continue
        direction = _registration_direction(
            fragments[first_index], fragments[second_index], edge
        )
        axis = _direction_axis(direction)
        if axis is None:
            calibrated.append(edge)
            continue
        first = fragments[first_index]
        second = fragments[second_index]
        source_transform = _crop_to_source_pair_transform(
            first, second, edge.second_to_first
        )
        source_h, source_w = _source_shape(first)
        if axis == "horizontal":
            primary = step_ratios[axis] * float(source_w)
            source_transform[0, 2] = primary if direction == "right" else -primary
            source_transform[1, 2] = float(
                np.clip(source_transform[1, 2], -0.25 * source_h, 0.25 * source_h)
            )
        else:
            primary = step_ratios[axis] * float(source_h)
            source_transform[1, 2] = primary if direction == "down" else -primary
            source_transform[0, 2] = float(
                np.clip(source_transform[0, 2], -0.25 * source_w, 0.25 * source_w)
            )
        transform = _source_to_crop_pair_transform(first, second, source_transform)
        overlap, intensity_ncc, gradient_ncc = _pair_metrics(
            first.gray, first.mask, second.gray, second.mask, transform
        )
        calibrated.append(
            replace(
                edge,
                second_to_first=transform,
                overlap_ratio=float(overlap),
                intensity_ncc=float(intensity_ncc),
                gradient_ncc=float(gradient_ncc),
                review_required=True,
                selection_reason=f"sequence_weak_grid_step_calibrated_{axis}",
            )
        )
        changed_pairs.append([first_index, second_index])

    return calibrated, {
        "applied": bool(changed_pairs),
        "reason": "weak_sequence_grid_step_calibration",
        "strongEdgeCount": strong_count,
        "edgeCount": len(choices),
        "strongEdgeRatio": float(strong_ratio),
        "stepRatios": step_ratios,
        "evidenceRatios": evidence,
        "changedPairs": changed_pairs,
    }


def _resolve_sequence_chain_for_axis(
    pair_indices: list[int],
    candidate_sets: dict[tuple[int, int], list[PairRegistration]],
    fragments: list[Fragment],
    config: AssemblyConfig,
    run_axis: str,
) -> tuple[_SequenceState, list[dict[str, Any]]]:
    first_fragment_index = pair_indices[0]
    initial_pose = np.eye(3, dtype=np.float64)
    states = [
        _SequenceState(
            score=0.0,
            choices=[],
            adjustments=[],
            current_pose=initial_pose,
            boxes=[_frame_box(fragments[first_fragment_index], initial_pose)],
            last_direction=None,
            last_run_direction=None,
            last_cross_direction=None,
        )
    ]
    beam_width = max(4, int(config.frame_sequence_beam_width))
    per_pair_debug: list[dict[str, Any]] = []

    for first_index in pair_indices:
        second_index = first_index + 1
        pair = (first_index, second_index)
        choices = _sequence_choices_for_pair(candidate_sets[pair], config)
        per_pair_debug.append(
            {
                "firstIndex": first_index,
                "secondIndex": second_index,
                "candidateDirections": [edge.direction for edge in choices],
                "candidateScores": [float(edge.score) for edge in choices],
                "candidateMethods": [edge.method for edge in choices],
            }
        )
        expanded: list[_SequenceState] = []
        for state in states:
            for edge in choices:
                direction = _registration_direction(
                    fragments[first_index], fragments[second_index], edge
                )
                transition_adjustment = _sequence_transition_adjustment(
                    run_axis,
                    state.last_direction,
                    state.last_run_direction,
                    state.last_cross_direction,
                    direction,
                    config,
                )
                next_pose = state.current_pose @ edge.second_to_first
                next_box = _frame_box(fragments[second_index], next_pose)
                collision_penalty = 0.0
                # The immediately previous frame is expected to overlap at its
                # continuation strip. Earlier frames should not occupy almost
                # the same scanner cell.
                for previous_box in state.boxes[:-1]:
                    overlap = _box_overlap_ratio(previous_box, next_box)
                    if overlap > 0.30:
                        collision_penalty += (
                            overlap - 0.30
                        ) * float(config.frame_sequence_collision_penalty)
                strong_bonus = 0.20 if _is_strong_registration(edge, config) else 0.0
                edge_score = (
                    float(edge.score)
                    + transition_adjustment
                    + strong_bonus
                    - collision_penalty
                )
                last_run_direction = state.last_run_direction
                last_cross_direction = state.last_cross_direction
                if _direction_axis(direction) == run_axis:
                    last_run_direction = direction
                else:
                    last_cross_direction = direction
                expanded.append(
                    _SequenceState(
                        score=state.score + edge_score,
                        choices=state.choices + [edge],
                        adjustments=state.adjustments
                        + [transition_adjustment - collision_penalty],
                        current_pose=next_pose,
                        boxes=state.boxes + [next_box],
                        last_direction=direction,
                        last_run_direction=last_run_direction,
                        last_cross_direction=last_cross_direction,
                    )
                )
        expanded.sort(key=lambda item: item.score, reverse=True)
        states = expanded[:beam_width]

    return max(states, key=lambda item: item.score), per_pair_debug


def _resolve_sequence_chain(
    pair_indices: list[int],
    candidate_sets: dict[tuple[int, int], list[PairRegistration]],
    fragments: list[Fragment],
    config: AssemblyConfig,
) -> tuple[dict[tuple[int, int], PairRegistration], dict[str, Any]]:
    axis_results: dict[str, tuple[_SequenceState, list[dict[str, Any]]]] = {}
    for run_axis in ("horizontal", "vertical"):
        axis_results[run_axis] = _resolve_sequence_chain_for_axis(
            pair_indices, candidate_sets, fragments, config, run_axis
        )
    selected_run_axis, (best, per_pair_debug) = max(
        axis_results.items(), key=lambda item: item[1][0].score
    )
    calibrated_choices, step_calibration = _calibrate_weak_sequence_steps(
        pair_indices, best.choices, candidate_sets, fragments, config
    )
    best = replace(best, choices=calibrated_choices)

    resolved: dict[tuple[int, int], PairRegistration] = {}
    selected_debug: list[dict[str, Any]] = []
    for first_index, edge, adjustment in zip(
        pair_indices, best.choices, best.adjustments
    ):
        second_index = first_index + 1
        strong = _is_strong_registration(edge, config)
        confidence = float(
            np.clip(
                edge.selection_confidence
                + max(adjustment, 0.0) * 0.65
                + (0.12 if strong else 0.0)
                + min(adjustment, 0.0) * 0.35,
                0.0,
                1.0,
            )
        )
        reason = (
            edge.selection_reason
            if strong
            else f"sequence_resolved_{selected_run_axis}_run_boundary_profile"
        )
        selected = replace(
            edge,
            direction=_registration_direction(
                fragments[first_index], fragments[second_index], edge
            ),
            selection_confidence=confidence,
            review_required=bool(
                not strong
                and confidence
                < float(config.frame_sequence_low_confidence_threshold)
            ),
            selection_reason=reason,
        )
        resolved[(first_index, second_index)] = selected
        selected_debug.append(
            {
                "firstIndex": first_index,
                "secondIndex": second_index,
                "direction": selected.direction,
                "method": selected.method,
                "localScore": float(edge.score),
                "sequenceAdjustment": float(adjustment),
                "selectionConfidence": confidence,
                "reviewRequired": selected.review_required,
            }
        )
    return resolved, {
        "startIndex": pair_indices[0],
        "endIndex": pair_indices[-1] + 1,
        "edgeCount": len(pair_indices),
        "beamWidth": max(4, int(config.frame_sequence_beam_width)),
        "runAxis": selected_run_axis,
        "axisScores": {
            axis: float(result[0].score) for axis, result in axis_results.items()
        },
        "stepCalibration": step_calibration,
        "bestSequenceScore": float(best.score),
        "pairs": per_pair_debug,
        "selected": selected_debug,
    }


def _resolve_adjacent_sequences(
    candidate_sets: dict[tuple[int, int], list[PairRegistration]],
    fragments: list[Fragment],
    config: AssemblyConfig,
) -> tuple[
    dict[tuple[int, int], PairRegistration],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    resolved: dict[tuple[int, int], PairRegistration] = {}
    diagnostics: list[dict[str, Any]] = []
    breaks: list[dict[str, Any]] = []
    if not bool(config.frame_sequence_enabled):
        return resolved, diagnostics, breaks

    eligible_edges: list[int] = []
    for first_index in range(len(fragments) - 1):
        pair = (first_index, first_index + 1)
        candidates = candidate_sets.get(pair, [])
        if not candidates:
            continue
        if not (_is_scanner_fragment(fragments[first_index]) and _is_scanner_fragment(fragments[first_index + 1])):
            continue
        strong = [edge for edge in candidates if _is_strong_registration(edge, config)]
        frame = [edge for edge in candidates if edge.method.startswith("frame_continuation")]
        maximum_frame_score = max(
            (edge.selection_confidence for edge in frame),
            default=0.0,
        )
        first_series = _scanner_series_key(fragments[first_index])
        second_series = _scanner_series_key(fragments[first_index + 1])
        series_changed = first_series != second_series
        if strong:
            eligible_edges.append(first_index)
        elif series_changed:
            breaks.append(
                {
                    "firstIndex": first_index,
                    "secondIndex": first_index + 1,
                    "maximumFrameScore": float(maximum_frame_score),
                    "threshold": float(config.frame_sequence_break_score),
                    "firstSeriesKey": first_series,
                    "secondSeriesKey": second_series,
                    "reason": "filename_acquisition_series_changed_without_strong_match",
                }
            )
        elif maximum_frame_score >= float(config.frame_sequence_break_score):
            eligible_edges.append(first_index)
        elif frame:
            breaks.append(
                {
                    "firstIndex": first_index,
                    "secondIndex": first_index + 1,
                    "maximumFrameScore": float(maximum_frame_score),
                    "threshold": float(config.frame_sequence_break_score),
                    "reason": "adjacent_pair_has_insufficient_image_or_boundary_evidence",
                }
            )

    chains: list[list[int]] = []
    current: list[int] = []
    for edge_index in eligible_edges:
        if current and edge_index != current[-1] + 1:
            chains.append(current)
            current = []
        current.append(edge_index)
    if current:
        chains.append(current)

    minimum_edges = max(1, int(config.frame_sequence_min_chain_length) - 1)
    for chain in chains:
        if len(chain) < minimum_edges:
            continue
        chain_resolved, chain_debug = _resolve_sequence_chain(
            chain, candidate_sets, fragments, config
        )
        resolved.update(chain_resolved)
        diagnostics.append(chain_debug)
    return resolved, diagnostics, breaks


def _extended_sift_anchors(
    fragments: list[Fragment],
    config: AssemblyConfig,
) -> list[PairRegistration]:
    if not bool(config.frame_extended_sift_enabled):
        return []
    window = max(2, int(config.frame_extended_sift_window))
    anchors: list[PairRegistration] = []
    for first_index in range(len(fragments)):
        if not _is_scanner_fragment(fragments[first_index]):
            continue
        for second_index in range(
            first_index + 2,
            min(len(fragments), first_index + window + 1),
        ):
            if not _is_scanner_fragment(fragments[second_index]):
                continue
            edge = _sift_candidate(
                fragments[first_index],
                fragments[second_index],
                config,
            )
            if edge is None or not _is_extended_sift_anchor(edge, config):
                continue
            anchors.append(
                replace(
                    edge,
                    direction=_registration_direction(
                        fragments[first_index], fragments[second_index], edge
                    ),
                    selection_confidence=_extended_sift_confidence(edge),
                    review_required=False,
                    selection_reason="extended_sift_anchor",
                )
            )
    return anchors


def _adjacent_path_transform(
    first_index: int,
    second_index: int,
    adjacent_edges: dict[tuple[int, int], PairRegistration],
) -> tuple[np.ndarray | None, list[tuple[int, int]]]:
    transform = np.eye(3, dtype=np.float64)
    path: list[tuple[int, int]] = []
    for index in range(first_index, second_index):
        pair = (index, index + 1)
        edge = adjacent_edges.get(pair)
        if edge is None:
            return None, path
        transform = transform @ edge.second_to_first
        path.append(pair)
    return transform, path


def _apply_cycle_consistency(
    anchors: list[PairRegistration],
    adjacent_edges: dict[tuple[int, int], PairRegistration],
    config: AssemblyConfig,
) -> tuple[
    list[PairRegistration],
    dict[tuple[int, int], PairRegistration],
    list[dict[str, Any]],
]:
    annotated: list[PairRegistration] = []
    diagnostics: list[dict[str, Any]] = []
    inconsistent_paths: list[list[tuple[int, int]]] = []
    for edge in anchors:
        predicted, path = _adjacent_path_transform(
            edge.first_index,
            edge.second_index,
            adjacent_edges,
        )
        if predicted is None:
            annotated.append(edge)
            diagnostics.append(
                {
                    "firstIndex": edge.first_index,
                    "secondIndex": edge.second_index,
                    "path": [[first, second] for first, second in path],
                    "pathAvailable": False,
                    "consistent": None,
                }
            )
            continue
        residual = np.linalg.inv(edge.second_to_first) @ predicted
        rotation_residual = abs(
            _normalize_angle(
                math.degrees(math.atan2(residual[1, 0], residual[0, 0]))
            )
        )
        translation_residual = float(np.linalg.norm(residual[:2, 2]))
        consistent = (
            translation_residual
            <= float(config.frame_cycle_translation_tolerance_px)
            and rotation_residual
            <= float(config.frame_cycle_rotation_tolerance_deg)
        )
        annotated_edge = replace(
            edge,
            cycle_path_length=len(path),
            cycle_translation_residual_px=translation_residual,
            cycle_rotation_residual_deg=rotation_residual,
            cycle_consistent=consistent,
            selection_reason=(
                "extended_sift_anchor_cycle_consistent"
                if consistent
                else "extended_sift_anchor_cycle_conflict"
            ),
        )
        annotated.append(annotated_edge)
        diagnostics.append(
            {
                "firstIndex": edge.first_index,
                "secondIndex": edge.second_index,
                "path": [[first, second] for first, second in path],
                "pathAvailable": True,
                "translationResidualPx": translation_residual,
                "rotationResidualDeg": rotation_residual,
                "translationTolerancePx": float(
                    config.frame_cycle_translation_tolerance_px
                ),
                "rotationToleranceDeg": float(
                    config.frame_cycle_rotation_tolerance_deg
                ),
                "consistent": consistent,
            }
        )
        if not consistent:
            inconsistent_paths.append(path)

    updated_adjacent = dict(adjacent_edges)
    penalty = float(config.frame_cycle_inconsistent_edge_penalty)
    for path in inconsistent_paths:
        for pair in path:
            edge = updated_adjacent.get(pair)
            if edge is None or _is_strong_registration(edge, config):
                continue
            updated_adjacent[pair] = replace(
                edge,
                selection_confidence=float(
                    np.clip(edge.selection_confidence - penalty, 0.0, 1.0)
                ),
                review_required=True,
                selection_reason="sequence_edge_penalized_by_cycle_conflict",
            )
    return annotated, updated_adjacent, diagnostics


def compute_pairwise_registrations(
    fragments: list[Fragment],
    config: AssemblyConfig,
) -> tuple[list[PairRegistration], dict[str, Any]]:
    longest_scanner_run = _longest_scanner_run(fragments)
    use_long_sequence = (
        bool(config.frame_sequence_enabled)
        and longest_scanner_run >= int(config.frame_sequence_min_chain_length)
    )
    candidate_sets: dict[tuple[int, int], list[PairRegistration]] = {}
    for first_index, second_index in _candidate_pairs(len(fragments), config):
        candidate_sets[(first_index, second_index)] = _registration_candidates(
            fragments[first_index],
            fragments[second_index],
            config,
            use_boundary_continuation=use_long_sequence,
        )

    if use_long_sequence:
        sequence_resolved, sequence_diagnostics, sequence_breaks = _resolve_adjacent_sequences(
            candidate_sets, fragments, config
        )
    else:
        sequence_resolved, sequence_diagnostics, sequence_breaks = {}, [], []
    sequence_break_pairs = {
        (int(item["firstIndex"]), int(item["secondIndex"]))
        for item in sequence_breaks
    }
    selected_by_pair: dict[tuple[int, int], PairRegistration] = {}
    for pair in sorted(candidate_sets):
        if pair in sequence_resolved:
            selected_by_pair[pair] = sequence_resolved[pair]
            continue
        candidates = candidate_sets[pair]
        candidates.sort(
            key=lambda candidate: (
                int(_is_strong_registration(candidate, config)),
                candidate.selection_confidence,
                candidate.score,
            ),
            reverse=True,
        )
        selected = candidates[0]
        if pair in sequence_break_pairs and not _is_strong_registration(selected, config):
            selected = replace(
                selected,
                selection_confidence=0.0,
                review_required=True,
                selection_reason="sequence_break_rejected",
            )
        selected_by_pair[pair] = selected

    extended_anchors = (
        [
            edge
            for edge in _extended_sift_anchors(fragments, config)
            if (edge.first_index, edge.second_index) not in selected_by_pair
        ]
        if use_long_sequence
        else []
    )
    cycle_candidates = [
        edge
        for edge in list(selected_by_pair.values()) + extended_anchors
        if edge.second_index - edge.first_index > 1
        and _is_extended_sift_anchor(edge, config)
    ]
    annotated_anchors, sequence_resolved, cycle_diagnostics = _apply_cycle_consistency(
        cycle_candidates,
        sequence_resolved,
        config,
    )
    annotated_by_pair = {
        (edge.first_index, edge.second_index): edge
        for edge in annotated_anchors
    }
    for pair in list(selected_by_pair):
        if pair in sequence_resolved:
            selected_by_pair[pair] = sequence_resolved[pair]
        if pair in annotated_by_pair:
            selected_by_pair[pair] = annotated_by_pair[pair]
    extended_anchors = [
        annotated_by_pair.get((edge.first_index, edge.second_index), edge)
        for edge in extended_anchors
    ]
    registrations = [selected_by_pair[pair] for pair in sorted(selected_by_pair)]
    registrations.extend(extended_anchors)

    alternatives = {
        f"{first_index}:{second_index}": [
            edge.to_dict()
            for edge in sorted(
                candidates,
                key=lambda candidate: (
                    int(_is_strong_registration(candidate, config)),
                    candidate.selection_confidence,
                    candidate.score,
                ),
                reverse=True,
            )
        ]
        for (first_index, second_index), candidates in candidate_sets.items()
    }
    return registrations, {
        "registrationMode": (
            "long_scanner_sequence" if use_long_sequence else "legacy_short_sequence"
        ),
        "longestScannerRun": int(longest_scanner_run),
        "candidateAlternatives": alternatives,
        "sequenceResolution": sequence_diagnostics,
        "sequenceBreaks": sequence_breaks,
        "extendedSiftAnchors": [edge.to_dict() for edge in extended_anchors],
        "cycleConsistency": cycle_diagnostics,
    }

def _registration_plausible(
    edge: PairRegistration,
    config: AssemblyConfig,
    fragments: list[Fragment],
) -> bool:
    if edge.selection_reason == "sequence_break_rejected":
        return False

    # Preserve the validated legacy behaviour for short/simple datasets.
    if edge.method.startswith("legacy_frame_continuation"):
        return (
            edge.score >= float(config.pair_min_score)
            and edge.overlap_ratio >= 0.01
        )

    # Long scanner runs use only evidence-scored, same-direction continuation.
    if edge.method.startswith("frame_continuation_boundary"):
        angle = abs(
            _normalize_angle(
                math.degrees(
                    math.atan2(
                        edge.second_to_first[1, 0],
                        edge.second_to_first[0, 0],
                    )
                )
            )
        )
        sequence_supported = (
            edge.selection_reason.startswith("sequence_resolved")
            or edge.selection_reason.startswith("sequence_edge_penalized")
        )
        if not sequence_supported and edge.boundary_reliability < 0.50:
            return False
        return (
            edge.selection_confidence >= float(config.frame_min_accept_score)
            and angle <= float(config.frame_same_direction_max_deg)
        )

    if edge.score < config.pair_min_score or edge.overlap_ratio < config.pair_min_overlap:
        return False
    if edge.method.startswith("sift"):
        # SIFT/RANSAC already enforces fixed scale and minimum inliers when the
        # candidate is generated.  Non-adjacent anchors have stricter checks in
        # _is_extended_sift_anchor().
        return True
    if edge.method.startswith("source_constellation_translation_consensus"):
        return _is_strong_registration(edge, config)
    if edge.method.startswith("source_frame_transform_propagation"):
        return _is_strong_registration(edge, config)
    if edge.method.startswith("source_boundary_profile"):
        return _is_strong_registration(edge, config)

    first_clipped = bool(
        fragments[edge.first_index].diagnostics.get("touches_frame", False)
    )
    second_clipped = bool(
        fragments[edge.second_index].diagnostics.get("touches_frame", False)
    )
    return first_clipped and second_clipped


def _registration_evidence_priority(
    edge: PairRegistration, config: AssemblyConfig
) -> int:
    """Return a method-level evidence tier for component graph selection.

    Raw scores from different registration families are not calibrated to the
    same scale.  In particular, boundary-profile consensus often produces a
    score near 1.0 even though it is weaker evidence than direct SIFT/RANSAC.
    The spanning forest must therefore preserve direct image-supported edges
    before adding source-level propagation or boundary continuation edges.
    """
    if edge.method.startswith("sift") and _is_strong_registration(edge, config):
        return 500
    if edge.method.startswith("phase") and _is_strong_registration(edge, config):
        return 450
    if edge.method.startswith("source_constellation_translation_consensus"):
        return 400
    if edge.method.startswith("source_frame_transform_propagation"):
        return 300
    if edge.method.startswith("source_boundary_profile_anchor_supported"):
        return 250
    if edge.method.startswith("source_boundary_profile_consensus"):
        return 200
    if edge.method.startswith("frame_continuation_boundary"):
        return 100
    if edge.method.startswith("legacy_frame_continuation"):
        return 50
    return 0


def _maximum_spanning_forest(
    fragment_count: int,
    registrations: list[PairRegistration],
    config: AssemblyConfig,
    fragments: list[Fragment],
) -> tuple[list[PairRegistration], dict[str, Any]]:
    parent = list(range(fragment_count))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(first: int, second: int) -> bool:
        root_first, root_second = find(first), find(second)
        if root_first == root_second:
            return False
        parent[root_second] = root_first
        return True

    accepted = [
        edge
        for edge in registrations
        if _registration_plausible(edge, config, fragments)
    ]
    evidence_priority_mode = any(
        _registration_evidence_priority(edge, config) > 0
        and not edge.method.startswith("legacy_frame_continuation")
        for edge in registrations
    )
    if evidence_priority_mode:
        accepted.sort(
            key=lambda edge: (
                _registration_evidence_priority(edge, config),
                int(_is_strong_registration(edge, config)),
                edge.selection_confidence,
                edge.score,
                edge.overlap_ratio,
            ),
            reverse=True,
        )
    else:
        accepted.sort(key=lambda edge: edge.score, reverse=True)

    selected: list[PairRegistration] = []
    for edge in accepted:
        if union(edge.first_index, edge.second_index):
            selected.append(edge)

    strong_selected_count = sum(
        1 for edge in selected if _is_strong_registration(edge, config)
    )
    weak_selected_count = len(selected) - strong_selected_count
    candidate_method_counts: dict[str, int] = {}
    selected_method_counts: dict[str, int] = {}
    for edge in accepted:
        candidate_method_counts[edge.method] = candidate_method_counts.get(edge.method, 0) + 1
    for edge in selected:
        selected_method_counts[edge.method] = selected_method_counts.get(edge.method, 0) + 1
    return selected, {
        "mode": (
            "registration_evidence_priority"
            if evidence_priority_mode
            else "legacy_score_priority"
        ),
        "plausibleCandidateCount": len(accepted),
        "candidateMethodCounts": candidate_method_counts,
        "selectedMethodCounts": selected_method_counts,
        "strongCandidateCount": sum(
            1 for edge in accepted if _is_strong_registration(edge, config)
        ),
        "weakCandidateCount": sum(
            1 for edge in accepted if not _is_strong_registration(edge, config)
        ),
        "strongSelectedCount": strong_selected_count,
        "weakSelectedCount": weak_selected_count,
        "weakSelectedDominates": weak_selected_count > strong_selected_count,
    }


def _pose_components(fragment_count: int, tree_edges: list[PairRegistration], fragments: list[Fragment]) -> tuple[list[np.ndarray], list[list[int]]]:
    adjacency: dict[int, list[tuple[int, np.ndarray]]] = {index: [] for index in range(fragment_count)}
    for edge in tree_edges:
        adjacency[edge.first_index].append((edge.second_index, edge.second_to_first))
        adjacency[edge.second_index].append((edge.first_index, np.linalg.inv(edge.second_to_first)))

    poses: list[np.ndarray | None] = [None] * fragment_count
    components: list[list[int]] = []
    for start in range(fragment_count):
        if poses[start] is not None:
            continue
        poses[start] = np.eye(3, dtype=np.float64)
        component: list[int] = []
        queue = [start]
        while queue:
            current = queue.pop(0)
            component.append(current)
            assert poses[current] is not None
            for neighbor, neighbor_to_current in adjacency[current]:
                if poses[neighbor] is None:
                    poses[neighbor] = poses[current] @ neighbor_to_current
                    queue.append(neighbor)
        components.append(component)

    # Pack disconnected components side by side. They remain independently
    # movable in Konva and the report flags them as unresolved.
    component_offset_x = 0.0
    packed: list[np.ndarray] = [np.eye(3, dtype=np.float64) for _ in range(fragment_count)]
    for component in components:
        transformed_points: list[np.ndarray] = []
        for index in component:
            assert poses[index] is not None
            transformed_points.append(_transform_points(poses[index], _fragment_corners(fragments[index])))
        points = np.vstack(transformed_points)
        minimum = points.min(axis=0)
        maximum = points.max(axis=0)
        shift = _translation(component_offset_x - minimum[0], -minimum[1])
        for index in component:
            packed[index] = shift @ poses[index]
        component_offset_x += float(maximum[0] - minimum[0]) + 40.0
    return packed, components


def _pose_angle(pose: np.ndarray) -> float:
    return float(math.atan2(pose[1, 0], pose[0, 0]))


def _edge_pose_graph_weight(edge: PairRegistration, config: AssemblyConfig) -> float:
    if edge.method.startswith("sift") and _is_strong_registration(edge, config):
        weight = 0.8 + 1.2 * float(edge.selection_confidence)
        if edge.cycle_consistent is False:
            weight *= 0.80
        return max(weight, 0.2)
    if edge.method.startswith("phase") and _is_strong_registration(edge, config):
        return 0.55 + 0.75 * float(edge.selection_confidence)
    if edge.method.startswith("source_constellation_translation_consensus"):
        return 0.65 + 0.85 * float(edge.selection_confidence)
    if edge.method.startswith("source_frame_transform_propagation"):
        return 0.60 + 0.80 * float(edge.selection_confidence)
    if edge.method.startswith("source_boundary_profile"):
        return 0.35 + 0.55 * float(edge.selection_confidence)
    if edge.method.startswith("frame_continuation"):
        weight = float(config.frame_pose_graph_weak_edge_weight) * (
            0.5 + float(edge.selection_confidence)
        )
        if edge.review_required:
            weight *= 0.45
        return max(weight, 0.02)
    return 0.05


def _solve_weighted_system(
    rows: list[np.ndarray],
    values: list[float],
    weights: list[float],
    variable_count: int,
) -> np.ndarray:
    if variable_count <= 0:
        return np.zeros((0,), dtype=np.float64)
    if not rows:
        return np.zeros((variable_count,), dtype=np.float64)
    matrix = np.vstack(rows).astype(np.float64)
    vector = np.asarray(values, dtype=np.float64)
    root_weights = np.sqrt(np.maximum(np.asarray(weights, dtype=np.float64), 1e-9))
    matrix *= root_weights[:, None]
    vector *= root_weights
    solution, _, _, _ = np.linalg.lstsq(matrix, vector, rcond=None)
    return solution


def _refine_component_pose_graph(
    component: list[int],
    initial_poses: list[np.ndarray],
    constraints: list[PairRegistration],
    config: AssemblyConfig,
) -> tuple[dict[int, np.ndarray], dict[str, Any]]:
    if len(component) <= 1 or not constraints:
        return {index: initial_poses[index] for index in component}, {
            "fragmentIndices": component,
            "constraintCount": len(constraints),
            "optimized": False,
        }

    root = min(component)
    variable_nodes = [index for index in component if index != root]
    variable_index = {node: position for position, node in enumerate(variable_nodes)}
    theta = {index: _pose_angle(initial_poses[index]) for index in component}
    position = {
        index: initial_poses[index][:2, 2].astype(np.float64).copy()
        for index in component
    }
    initial_theta = dict(theta)
    initial_position = {index: value.copy() for index, value in position.items()}

    strong_sift_rotation_deg = [
        abs(
            _normalize_angle(
                math.degrees(
                    math.atan2(
                        edge.second_to_first[1, 0],
                        edge.second_to_first[0, 0],
                    )
                )
            )
        )
        for edge in constraints
        if edge.method.startswith("sift") and _is_strong_registration(edge, config)
    ]
    same_direction_orientation_lock = (
        len(component) >= int(config.frame_pose_graph_orientation_lock_min_fragments)
        and len(strong_sift_rotation_deg)
        >= int(config.frame_pose_graph_orientation_lock_min_sift_edges)
        and float(np.quantile(strong_sift_rotation_deg, 0.90))
        <= float(config.frame_pose_graph_orientation_lock_max_edge_deg)
    )
    shared_orientation = float(theta[root])

    edge_records: list[tuple[PairRegistration, float, float]] = []
    for edge in constraints:
        relative_angle = math.atan2(
            edge.second_to_first[1, 0], edge.second_to_first[0, 0]
        )
        initial_difference = theta[edge.second_index] - theta[edge.first_index]
        relative_angle += round(
            (initial_difference - relative_angle) / (2.0 * math.pi)
        ) * (2.0 * math.pi)
        edge_records.append(
            (edge, relative_angle, _edge_pose_graph_weight(edge, config))
        )

    robust = np.ones((len(edge_records),), dtype=np.float64)
    prior_weight = max(float(config.frame_pose_graph_prior_weight), 1e-6)
    iteration_count = max(1, int(config.frame_pose_graph_iterations))
    translation_huber = max(
        float(config.frame_pose_graph_translation_huber_px), 1.0
    )
    rotation_huber = math.radians(
        max(float(config.frame_pose_graph_rotation_huber_deg), 0.1)
    )

    def add_difference_row(
        first_index: int,
        second_index: int,
        target: float,
        fixed_values: dict[int, float],
    ) -> tuple[np.ndarray, float]:
        row = np.zeros((len(variable_nodes),), dtype=np.float64)
        value = float(target)
        if first_index == root:
            value += float(fixed_values[root])
        else:
            row[variable_index[first_index]] -= 1.0
        if second_index == root:
            value -= float(fixed_values[root])
        else:
            row[variable_index[second_index]] += 1.0
        return row, value

    for _ in range(iteration_count):
        angle_rows: list[np.ndarray] = []
        angle_values: list[float] = []
        angle_weights: list[float] = []
        for record_index, (edge, relative_angle, base_weight) in enumerate(edge_records):
            row, value = add_difference_row(
                edge.first_index,
                edge.second_index,
                relative_angle,
                {root: theta[root]},
            )
            angle_rows.append(row)
            angle_values.append(value)
            angle_weights.append(base_weight * robust[record_index])
        for node in variable_nodes:
            row = np.zeros((len(variable_nodes),), dtype=np.float64)
            row[variable_index[node]] = 1.0
            angle_rows.append(row)
            angle_values.append(initial_theta[node])
            angle_weights.append(prior_weight)
            if same_direction_orientation_lock:
                angle_rows.append(row.copy())
                angle_values.append(shared_orientation)
                angle_weights.append(
                    float(config.frame_pose_graph_orientation_lock_weight)
                )
        angle_solution = _solve_weighted_system(
            angle_rows,
            angle_values,
            angle_weights,
            len(variable_nodes),
        )
        for node in variable_nodes:
            theta[node] = float(angle_solution[variable_index[node]])

        x_rows: list[np.ndarray] = []
        y_rows: list[np.ndarray] = []
        x_values: list[float] = []
        y_values: list[float] = []
        translation_weights: list[float] = []
        for record_index, (edge, _, base_weight) in enumerate(edge_records):
            cosine = math.cos(theta[edge.first_index])
            sine = math.sin(theta[edge.first_index])
            relative_translation = edge.second_to_first[:2, 2].astype(np.float64)
            expected = np.array(
                [
                    cosine * relative_translation[0] - sine * relative_translation[1],
                    sine * relative_translation[0] + cosine * relative_translation[1],
                ],
                dtype=np.float64,
            )
            x_fixed = {root: float(position[root][0])}
            y_fixed = {root: float(position[root][1])}
            row_x, value_x = add_difference_row(
                edge.first_index,
                edge.second_index,
                float(expected[0]),
                x_fixed,
            )
            row_y, value_y = add_difference_row(
                edge.first_index,
                edge.second_index,
                float(expected[1]),
                y_fixed,
            )
            x_rows.append(row_x)
            y_rows.append(row_y)
            x_values.append(value_x)
            y_values.append(value_y)
            translation_weights.append(base_weight * robust[record_index])
        for node in variable_nodes:
            row = np.zeros((len(variable_nodes),), dtype=np.float64)
            row[variable_index[node]] = 1.0
            x_rows.append(row)
            y_rows.append(row.copy())
            x_values.append(float(initial_position[node][0]))
            y_values.append(float(initial_position[node][1]))
            translation_weights.append(prior_weight)
        x_solution = _solve_weighted_system(
            x_rows,
            x_values,
            translation_weights,
            len(variable_nodes),
        )
        y_solution = _solve_weighted_system(
            y_rows,
            y_values,
            translation_weights,
            len(variable_nodes),
        )
        for node in variable_nodes:
            position[node] = np.array(
                [
                    x_solution[variable_index[node]],
                    y_solution[variable_index[node]],
                ],
                dtype=np.float64,
            )

        for record_index, (edge, relative_angle, _) in enumerate(edge_records):
            angle_residual = abs(
                math.atan2(
                    math.sin(
                        theta[edge.second_index]
                        - theta[edge.first_index]
                        - relative_angle
                    ),
                    math.cos(
                        theta[edge.second_index]
                        - theta[edge.first_index]
                        - relative_angle
                    ),
                )
            )
            cosine = math.cos(theta[edge.first_index])
            sine = math.sin(theta[edge.first_index])
            relative_translation = edge.second_to_first[:2, 2].astype(np.float64)
            expected = np.array(
                [
                    cosine * relative_translation[0] - sine * relative_translation[1],
                    sine * relative_translation[0] + cosine * relative_translation[1],
                ]
            )
            translation_residual = float(
                np.linalg.norm(
                    position[edge.second_index]
                    - position[edge.first_index]
                    - expected
                )
            )
            normalized_residual = max(
                angle_residual / rotation_huber,
                translation_residual / translation_huber,
            )
            robust[record_index] = (
                1.0 if normalized_residual <= 1.0 else 1.0 / normalized_residual
            )

    refined: dict[int, np.ndarray] = {}
    residuals: list[dict[str, Any]] = []
    for index in component:
        cosine = math.cos(theta[index])
        sine = math.sin(theta[index])
        refined[index] = np.array(
            [
                [cosine, -sine, position[index][0]],
                [sine, cosine, position[index][1]],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )
    for record_index, (edge, relative_angle, base_weight) in enumerate(edge_records):
        angle_residual = abs(
            math.degrees(
                math.atan2(
                    math.sin(
                        theta[edge.second_index]
                        - theta[edge.first_index]
                        - relative_angle
                    ),
                    math.cos(
                        theta[edge.second_index]
                        - theta[edge.first_index]
                        - relative_angle
                    ),
                )
            )
        )
        cosine = math.cos(theta[edge.first_index])
        sine = math.sin(theta[edge.first_index])
        relative_translation = edge.second_to_first[:2, 2].astype(np.float64)
        expected = np.array(
            [
                cosine * relative_translation[0] - sine * relative_translation[1],
                sine * relative_translation[0] + cosine * relative_translation[1],
            ]
        )
        translation_residual = float(
            np.linalg.norm(
                position[edge.second_index]
                - position[edge.first_index]
                - expected
            )
        )
        residuals.append(
            {
                "firstIndex": edge.first_index,
                "secondIndex": edge.second_index,
                "method": edge.method,
                "translationResidualPx": translation_residual,
                "rotationResidualDeg": angle_residual,
                "baseWeight": float(base_weight),
                "robustWeight": float(robust[record_index]),
            }
        )
    return refined, {
        "fragmentIndices": component,
        "constraintCount": len(edge_records),
        "optimized": True,
        "rootIndex": root,
        "sameDirectionOrientationLock": bool(same_direction_orientation_lock),
        "strongSiftRotationDeg": [float(value) for value in strong_sift_rotation_deg],
        "sharedOrientationDeg": float(math.degrees(shared_orientation)),
        "residuals": residuals,
    }


def _refine_poses_with_pose_graph(
    poses: list[np.ndarray],
    components: list[list[int]],
    registrations: list[PairRegistration],
    fragments: list[Fragment],
    config: AssemblyConfig,
) -> tuple[list[np.ndarray], list[dict[str, Any]]]:
    if not bool(config.frame_pose_graph_enabled):
        return poses, []
    refined = [pose.copy() for pose in poses]
    diagnostics: list[dict[str, Any]] = []
    minimum_scanner_count = max(2, int(config.frame_sequence_min_chain_length))
    for component in components:
        scanner_count = sum(1 for index in component if _is_scanner_fragment(fragments[index]))
        if scanner_count < minimum_scanner_count:
            diagnostics.append(
                {
                    "fragmentIndices": component,
                    "constraintCount": 0,
                    "optimized": False,
                    "reason": "component_below_long_sequence_threshold",
                    "scannerFragmentCount": int(scanner_count),
                    "minimumScannerFragmentCount": int(minimum_scanner_count),
                }
            )
            continue
        members = set(component)
        constraints = [
            edge
            for edge in registrations
            if edge.first_index in members
            and edge.second_index in members
            and _registration_plausible(edge, config, fragments)
        ]
        strong_constraints = [
            edge for edge in constraints if _is_strong_registration(edge, config)
        ]
        strong_nodes = {
            index
            for edge in strong_constraints
            for index in (edge.first_index, edge.second_index)
        }
        strong_node_coverage = len(strong_nodes) / max(len(component), 1)
        minimum_strong_edges = max(1, int(config.frame_pose_graph_min_strong_edges))
        minimum_strong_coverage = float(
            config.frame_pose_graph_min_strong_node_coverage
        )
        if (
            len(strong_constraints) < minimum_strong_edges
            or strong_node_coverage < minimum_strong_coverage
        ):
            diagnostics.append(
                {
                    "fragmentIndices": component,
                    "constraintCount": len(constraints),
                    "optimized": False,
                    "reason": "insufficient_image_supported_pose_graph_coverage",
                    "scannerFragmentCount": int(scanner_count),
                    "strongConstraintCount": len(strong_constraints),
                    "minimumStrongConstraintCount": minimum_strong_edges,
                    "strongNodeCoverage": float(strong_node_coverage),
                    "minimumStrongNodeCoverage": minimum_strong_coverage,
                }
            )
            continue
        component_poses, component_debug = _refine_component_pose_graph(
            component,
            refined,
            constraints,
            config,
        )
        component_debug["strongConstraintCount"] = len(strong_constraints)
        component_debug["strongNodeCoverage"] = float(strong_node_coverage)
        for index, pose in component_poses.items():
            refined[index] = pose
        diagnostics.append(component_debug)
    return refined, diagnostics


def normalize_poses_to_canvas(fragments: list[Fragment], poses: list[np.ndarray], margin: int = 20) -> tuple[list[np.ndarray], tuple[int, int]]:
    all_points = np.vstack([
        _transform_points(pose, _fragment_corners(fragment))
        for fragment, pose in zip(fragments, poses)
    ])
    minimum = np.floor(all_points.min(axis=0) - margin)
    maximum = np.ceil(all_points.max(axis=0) + margin)
    shift = _translation(-minimum[0], -minimum[1])
    normalized = [shift @ pose for pose in poses]
    size = np.maximum((maximum - minimum).astype(int), 1)
    return normalized, (int(size[0]), int(size[1]))


def render_mosaic(
    fragments: list[Fragment],
    poses: list[np.ndarray],
    canvas_size: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    canvas_width, canvas_height = canvas_size
    output_channels = 3 if any(fragment.image.ndim == 3 and fragment.image.shape[2] >= 3 for fragment in fragments) else 1
    output_dtype = np.result_type(*[fragment.image.dtype for fragment in fragments])
    output_shape = (canvas_height, canvas_width) if output_channels == 1 else (canvas_height, canvas_width, 3)
    mosaic = np.zeros(output_shape, dtype=output_dtype)
    owner = np.full((canvas_height, canvas_width), -1, dtype=np.int32)
    best_weight = np.full((canvas_height, canvas_width), -1.0, dtype=np.float32)
    counts = np.zeros((canvas_height, canvas_width), dtype=np.uint16)

    for fragment, pose in zip(fragments, poses):
        image = ensure_output_channels(fragment.image, output_channels, output_dtype)
        mask = (fragment.mask > 0).astype(np.uint8) * 255
        distance = cv2.distanceTransform((mask > 0).astype(np.uint8), cv2.DIST_L2, 3)
        warped_mask = cv2.warpAffine(mask, pose[:2].astype(np.float32), canvas_size, flags=cv2.INTER_NEAREST) > 0
        warped_image = cv2.warpAffine(
            image,
            pose[:2].astype(np.float32),
            canvas_size,
            flags=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        warped_weight = cv2.warpAffine(
            distance.astype(np.float32),
            pose[:2].astype(np.float32),
            canvas_size,
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        counts[warped_mask] += 1
        write = warped_mask & (warped_weight > best_weight)
        if output_channels == 1:
            mosaic[write] = warped_image[write]
        else:
            mosaic[write, :] = warped_image[write, :]
        owner[write] = fragment.index
        best_weight[write] = warped_weight[write]
    return mosaic, (owner >= 0).astype(np.uint8) * 255, counts, owner


def _boundary_f1(first: np.ndarray, second: np.ndarray, tolerance: int = 2) -> float:
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    first_edge = cv2.morphologyEx((first > 0).astype(np.uint8), cv2.MORPH_GRADIENT, kernel)
    second_edge = cv2.morphologyEx((second > 0).astype(np.uint8), cv2.MORPH_GRADIENT, kernel)
    if first_edge.sum() == 0 or second_edge.sum() == 0:
        return 0.0
    tolerance_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * tolerance + 1, 2 * tolerance + 1))
    first_dilated = cv2.dilate(first_edge, tolerance_kernel)
    second_dilated = cv2.dilate(second_edge, tolerance_kernel)
    precision = float(np.count_nonzero(first_edge & second_dilated)) / max(int(first_edge.sum()), 1)
    recall = float(np.count_nonzero(second_edge & first_dilated)) / max(int(second_edge.sum()), 1)
    return float(2.0 * precision * recall / max(precision + recall, 1e-9))



def _component_mask_records(mask: np.ndarray, minimum_area_ratio: float = 0.001) -> list[dict[str, Any]]:
    count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
    if count <= 1:
        return []
    areas = stats[1:, cv2.CC_STAT_AREA]
    largest = int(areas.max())
    records: list[dict[str, Any]] = []
    for label in range(1, count):
        x, y, width, height, area = [int(value) for value in stats[label]]
        if area < max(12, int(round(largest * minimum_area_ratio))):
            continue
        component = np.zeros_like(mask)
        component[labels == label] = 255
        records.append(
            {
                "mask": component,
                "bbox": (x, y, width, height),
                "area": area,
                "aspect": width / max(height, 1),
                "angle": principal_angle_deg(component),
            }
        )
    records.sort(key=lambda item: item["area"], reverse=True)
    return records


def _largest_contour(mask: np.ndarray) -> np.ndarray | None:
    contours, _ = cv2.findContours((mask > 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    return max(contours, key=cv2.contourArea)


def _component_assignment_cost(xray: dict[str, Any], reference: dict[str, Any], total_xray: int, total_reference: int) -> float:
    xray_fraction = xray["area"] / max(total_xray, 1)
    reference_fraction = reference["area"] / max(total_reference, 1)
    area_cost = abs(math.log(max(xray_fraction, 1e-9) / max(reference_fraction, 1e-9)))
    aspect_cost = abs(math.log(max(float(xray["aspect"]), 1e-6) / max(float(reference["aspect"]), 1e-6)))
    contour_xray = _largest_contour(xray["mask"])
    contour_reference = _largest_contour(reference["mask"])
    shape_cost = 2.0
    if contour_xray is not None and contour_reference is not None:
        shape_cost = min(float(cv2.matchShapes(contour_xray, contour_reference, cv2.CONTOURS_MATCH_I1, 0.0)), 4.0)
    return 1.2 * area_cost + 0.45 * aspect_cost + 0.35 * shape_cost


def _assign_components(xray_records: list[dict[str, Any]], reference_records: list[dict[str, Any]]) -> list[tuple[int, int, float]]:
    if not xray_records or not reference_records:
        return []
    total_xray = sum(int(record["area"]) for record in xray_records)
    total_reference = sum(int(record["area"]) for record in reference_records)
    candidates: list[tuple[float, int, int]] = []
    for xray_index, xray in enumerate(xray_records):
        for reference_index, reference in enumerate(reference_records):
            candidates.append(
                (
                    _component_assignment_cost(xray, reference, total_xray, total_reference),
                    xray_index,
                    reference_index,
                )
            )
    candidates.sort()
    used_xray: set[int] = set()
    used_reference: set[int] = set()
    assignments: list[tuple[int, int, float]] = []
    for cost, xray_index, reference_index in candidates:
        if xray_index in used_xray or reference_index in used_reference:
            continue
        used_xray.add(xray_index)
        used_reference.add(reference_index)
        assignments.append((xray_index, reference_index, float(cost)))
        if len(used_xray) == len(xray_records) or len(used_reference) == len(reference_records):
            break
    return assignments


def _component_local_bundle(
    fragments: list[Fragment],
    poses: list[np.ndarray],
    component_indices: list[int],
) -> dict[str, Any]:
    points = np.vstack([
        _transform_points(poses[index], _fragment_corners(fragments[index]))
        for index in component_indices
    ])
    minimum = np.floor(points.min(axis=0) - 8.0)
    maximum = np.ceil(points.max(axis=0) + 8.0)
    shift = _translation(-minimum[0], -minimum[1])
    local_poses = {index: shift @ poses[index] for index in component_indices}
    width, height = np.maximum((maximum - minimum).astype(int), 1)
    subset_fragments = [fragments[index] for index in component_indices]
    subset_poses = [local_poses[index] for index in component_indices]
    image, mask, counts, _ = render_mosaic(subset_fragments, subset_poses, (int(width), int(height)))
    bbox = cv2.boundingRect((mask > 0).astype(np.uint8))
    return {
        "indices": component_indices,
        "poses": local_poses,
        "image": image,
        "mask": mask,
        "counts": counts,
        "area": int(np.count_nonzero(mask)),
        "bbox": bbox,
        "aspect": bbox[2] / max(bbox[3], 1),
        "angle": principal_angle_deg(mask),
    }


def _align_component_mask_to_target(
    component_mask: np.ndarray,
    target_component: np.ndarray,
    config: AssemblyConfig,
) -> tuple[np.ndarray, dict[str, float]]:
    target_points = cv2.findNonZero((target_component > 0).astype(np.uint8))
    if target_points is None:
        raise ValueError("reference component mask가 비어 있습니다.")
    target_x, target_y, target_width, target_height = cv2.boundingRect(target_points)
    component_h, component_w = component_mask.shape
    crop_margin = max(20, int(round(0.30 * max(component_w, component_h, target_width, target_height))))
    crop_x0 = max(0, target_x - crop_margin)
    crop_y0 = max(0, target_y - crop_margin)
    crop_x1 = min(target_component.shape[1], target_x + target_width + crop_margin)
    crop_y1 = min(target_component.shape[0], target_y + target_height + crop_margin)
    target_crop = target_component[crop_y0:crop_y1, crop_x0:crop_x1]

    maximum_dimension = max(component_w, component_h, target_crop.shape[1], target_crop.shape[0])
    analysis_scale = min(1.0, float(config.global_alignment_max_dimension) / maximum_dimension)
    if analysis_scale < 0.999:
        component_small = cv2.resize(
            component_mask,
            (max(1, int(round(component_w * analysis_scale))), max(1, int(round(component_h * analysis_scale)))),
            interpolation=cv2.INTER_NEAREST,
        )
        target_small = cv2.resize(
            target_crop,
            (max(1, int(round(target_crop.shape[1] * analysis_scale))), max(1, int(round(target_crop.shape[0] * analysis_scale)))),
            interpolation=cv2.INTER_NEAREST,
        )
    else:
        component_small = component_mask
        target_small = target_crop
    small_h, small_w = component_small.shape
    target_small_h, target_small_w = target_small.shape

    base = _normalize_angle(principal_angle_deg(target_small) - principal_angle_deg(component_small))
    angles: set[float] = {0.0, 180.0, base, _normalize_angle(base + 180.0)}
    for seed in list(angles):
        for offset in range(-config.global_angle_radius_deg, config.global_angle_radius_deg + 1, config.global_angle_step_deg):
            angles.add(round(_normalize_angle(seed + offset), 6))

    def evaluate(angle: float) -> tuple[float, np.ndarray, float, float]:
        rotation, rotated_size = _rotation_bound_matrix(small_w, small_h, angle)
        rotated = cv2.warpAffine(
            component_small,
            rotation[:2].astype(np.float32),
            rotated_size,
            flags=cv2.INTER_NEAREST,
        )
        if rotated.shape[0] > target_small_h or rotated.shape[1] > target_small_w:
            return -1.0, np.eye(3), 0.0, 0.0
        response = cv2.matchTemplate(
            (target_small > 0).astype(np.float32),
            (rotated > 0).astype(np.float32),
            cv2.TM_CCORR,
        )
        _, _, _, location = cv2.minMaxLoc(response)
        placement = _translation(float(location[0]), float(location[1])) @ rotation
        placed = cv2.warpAffine(
            component_small,
            placement[:2].astype(np.float32),
            (target_small_w, target_small_h),
            flags=cv2.INTER_NEAREST,
        )
        intersection = int(np.count_nonzero((placed > 0) & (target_small > 0)))
        union = int(np.count_nonzero((placed > 0) | (target_small > 0)))
        iou = intersection / max(union, 1)
        boundary = _boundary_f1(placed, target_small, tolerance=max(1, int(config.boundary_tolerance_px)))
        return float(0.82 * iou + 0.18 * boundary), placement, float(iou), float(boundary)

    coarse = max((evaluate(angle) + (angle,) for angle in angles), key=lambda item: item[0])
    _, _, _, _, coarse_angle = coarse
    fine_angles = np.arange(
        coarse_angle - config.global_angle_step_deg,
        coarse_angle + config.global_angle_step_deg + 0.01,
        config.global_refine_angle_step_deg,
    )
    best = max((evaluate(float(angle)) + (float(angle),) for angle in fine_angles), key=lambda item: item[0])
    score, transform_small, iou, boundary, angle = best
    scale_matrix = np.diag([analysis_scale, analysis_scale, 1.0])
    transform_crop_full = np.linalg.inv(scale_matrix) @ transform_small @ scale_matrix
    transform_full = _translation(float(crop_x0), float(crop_y0)) @ transform_crop_full
    return transform_full, {
        "score": float(score),
        "iou": float(iou),
        "boundaryF1": float(boundary),
        "rotationDeg": float(_normalize_angle(angle)),
        "analysisScale": float(analysis_scale),
    }


def _minimum_cost_row_assignment_rectangular(cost_matrix: np.ndarray) -> list[int]:
    """Return one unique column per row for a rectangular minimum-cost matrix."""
    matrix = np.asarray(cost_matrix, dtype=np.float64)
    if matrix.ndim != 2:
        raise ValueError("cost_matrix는 2차원이어야 합니다.")
    row_count, column_count = matrix.shape
    if row_count == 0:
        return []
    if row_count > column_count:
        raise ValueError("row_count <= column_count가 필요합니다.")
    u = np.zeros(row_count + 1, dtype=np.float64)
    v = np.zeros(column_count + 1, dtype=np.float64)
    p = np.zeros(column_count + 1, dtype=np.int64)
    way = np.zeros(column_count + 1, dtype=np.int64)
    for row in range(1, row_count + 1):
        p[0] = row
        minimum = np.full(column_count + 1, np.inf, dtype=np.float64)
        used = np.zeros(column_count + 1, dtype=bool)
        column0 = 0
        while True:
            used[column0] = True
            row0 = int(p[column0])
            delta = np.inf
            column1 = 0
            for column in range(1, column_count + 1):
                if used[column]:
                    continue
                current = matrix[row0 - 1, column - 1] - u[row0] - v[column]
                if current < minimum[column]:
                    minimum[column] = current
                    way[column] = column0
                if minimum[column] < delta:
                    delta = minimum[column]
                    column1 = column
            for column in range(column_count + 1):
                if used[column]:
                    u[p[column]] += delta
                    v[column] -= delta
                else:
                    minimum[column] -= delta
            column0 = column1
            if p[column0] == 0:
                break
        while True:
            column1 = int(way[column0])
            p[column0] = p[column1]
            column0 = column1
            if column0 == 0:
                break
    assignment = [-1] * row_count
    for column in range(1, column_count + 1):
        if p[column] != 0:
            assignment[int(p[column]) - 1] = column - 1
    return assignment


def _conservative_complete_reference_assignments(
    xray_records: list[dict[str, Any]],
    reference_records: list[dict[str, Any]],
    config: AssemblyConfig,
) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]], dict[str, Any]]:
    if not xray_records or not reference_records:
        return [], {index: [] for index in range(len(xray_records))}, {
            "mode": "conservative_rigid_alignment_gated_assignment",
            "candidatePairCount": 0,
            "eligibleCandidatePairCount": 0,
            "selectedPairCount": 0,
        }
    total_xray = sum(int(record["area"]) for record in xray_records)
    total_reference = sum(int(record["area"]) for record in reference_records)
    top_k = max(1, int(config.complete_reference_candidate_top_k))
    maximum_cost = float(config.complete_reference_max_assignment_cost)
    minimum_score = float(config.complete_reference_min_alignment_score)
    minimum_iou = float(config.complete_reference_min_alignment_iou)
    unassigned_utility = float(config.complete_reference_unassigned_utility)
    cost_weight = float(config.complete_reference_assignment_cost_weight)
    candidates: dict[tuple[int, int], dict[str, Any]] = {}
    summaries: dict[int, list[dict[str, Any]]] = {
        index: [] for index in range(len(xray_records))
    }
    for xray_index, xray in enumerate(xray_records):
        ranked = sorted(
            (
                _component_assignment_cost(
                    xray, reference, total_xray, total_reference
                ),
                reference_index,
            )
            for reference_index, reference in enumerate(reference_records)
        )
        for assignment_cost, reference_index in ranked[:top_k]:
            transform, alignment = _align_component_mask_to_target(
                xray["mask"], reference_records[reference_index]["mask"], config
            )
            alignment_score = float(alignment.get("score", -1.0))
            alignment_iou = float(alignment.get("iou", 0.0))
            utility = float(alignment_score - cost_weight * assignment_cost)
            rejection_reasons: list[str] = []
            if float(assignment_cost) > maximum_cost:
                rejection_reasons.append("assignment_cost_above_maximum")
            if alignment_score < minimum_score:
                rejection_reasons.append("rigid_alignment_score_below_minimum")
            if alignment_iou < minimum_iou:
                rejection_reasons.append("rigid_alignment_iou_below_minimum")
            if utility < unassigned_utility:
                rejection_reasons.append("does_not_beat_explicit_unassignment")
            eligible = not rejection_reasons
            candidate = {
                "xrayComponentIndex": int(xray_index),
                "referenceComponentIndex": int(reference_index),
                "assignmentCost": float(assignment_cost),
                "alignmentUtility": utility,
                "alignment": alignment,
                "transform": transform,
                "eligible": eligible,
                "rejectionReasons": rejection_reasons,
            }
            candidates[(xray_index, reference_index)] = candidate
            summaries[xray_index].append(
                {
                    "referenceComponentIndex": int(reference_index),
                    "assignmentCost": float(assignment_cost),
                    "alignmentUtility": utility,
                    "alignment": alignment,
                    "eligible": eligible,
                    "rejectionReasons": rejection_reasons,
                }
            )
        summaries[xray_index].sort(
            key=lambda item: (
                -float(item["alignmentUtility"]),
                float(item["assignmentCost"]),
                int(item["referenceComponentIndex"]),
            )
        )

    row_count = len(xray_records)
    reference_count = len(reference_records)
    invalid_cost = 1.0e6
    cost_matrix = np.full(
        (row_count, reference_count + row_count),
        -unassigned_utility,
        dtype=np.float64,
    )
    cost_matrix[:, :reference_count] = invalid_cost
    for (xray_index, reference_index), candidate in candidates.items():
        if bool(candidate["eligible"]):
            cost_matrix[xray_index, reference_index] = -float(
                candidate["alignmentUtility"]
            )
    columns = _minimum_cost_row_assignment_rectangular(cost_matrix)
    selected: list[dict[str, Any]] = []
    for xray_index, column in enumerate(columns):
        if column < 0 or column >= reference_count:
            continue
        candidate = candidates.get((xray_index, column))
        if candidate is not None and bool(candidate["eligible"]):
            selected.append(candidate)
    selected.sort(key=lambda item: int(item["xrayComponentIndex"]))
    debug = {
        "mode": "conservative_rigid_alignment_gated_assignment",
        "candidateTopK": int(top_k),
        "maximumAssignmentCost": float(maximum_cost),
        "minimumAlignmentScore": float(minimum_score),
        "minimumAlignmentIou": float(minimum_iou),
        "explicitUnassignedUtility": float(unassigned_utility),
        "assignmentCostWeight": float(cost_weight),
        "candidatePairCount": int(len(candidates)),
        "eligibleCandidatePairCount": int(
            sum(bool(candidate["eligible"]) for candidate in candidates.values())
        ),
        "selectedPairCount": int(len(selected)),
    }
    return selected, summaries, debug

def align_components_to_reference(
    reference_image: np.ndarray,
    reference_mask: np.ndarray,
    fragments: list[Fragment],
    raw_poses: list[np.ndarray],
    components: list[list[int]],
    config: AssemblyConfig,
) -> tuple[list[np.ndarray], SearchGeometry, dict[str, Any], np.ndarray, np.ndarray, np.ndarray]:
    bundles = [_component_local_bundle(fragments, raw_poses, component) for component in components]
    total_xray_area = sum(int(bundle["area"]) for bundle in bundles)
    reference_area = int(np.count_nonzero(reference_mask))
    reference_scale = (
        float(config.reference_scale_override)
        if config.reference_scale_override is not None
        else math.sqrt(total_xray_area / max(reference_area * config.reference_fill_ratio, 1.0))
    )
    reference_h, reference_w = reference_mask.shape
    scaled_w = max(1, int(round(reference_w * reference_scale)))
    scaled_h = max(1, int(round(reference_h * reference_scale)))
    scaled_reference = cv2.resize(reference_mask, (scaled_w, scaled_h), interpolation=cv2.INTER_NEAREST)
    scaled_preview = cv2.resize(
        normalize_preview(reference_image),
        (scaled_w, scaled_h),
        interpolation=cv2.INTER_AREA if reference_scale < 1.0 else cv2.INTER_LINEAR,
    )
    largest_component_diagonal = max(
        int(math.ceil(math.hypot(bundle["mask"].shape[1], bundle["mask"].shape[0])))
        for bundle in bundles
    )
    margin = max(20, int(round(max(scaled_w, scaled_h, largest_component_diagonal) * config.canvas_margin_ratio)))
    canvas_w = max(scaled_w, largest_component_diagonal) + 2 * margin
    canvas_h = max(scaled_h, largest_component_diagonal) + 2 * margin
    target = np.zeros((canvas_h, canvas_w), dtype=np.uint8)
    preview = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    target_x = (canvas_w - scaled_w) // 2
    target_y = (canvas_h - scaled_h) // 2
    target[target_y : target_y + scaled_h, target_x : target_x + scaled_w] = scaled_reference
    preview[target_y : target_y + scaled_h, target_x : target_x + scaled_w] = scaled_preview

    reference_records = _component_mask_records(target, minimum_area_ratio=config.min_component_area_ratio)
    xray_records = [
        {
            "mask": bundle["mask"],
            "area": bundle["area"],
            "aspect": bundle["aspect"],
            "angle": bundle["angle"],
        }
        for bundle in bundles
    ]
    conservative_assignment = bool(
        config.complete_reference_conservative_assignment_enabled
    )
    if conservative_assignment:
        selected_assignments, candidate_summaries, assignment_debug = (
            _conservative_complete_reference_assignments(
                xray_records, reference_records, config
            )
        )
    else:
        legacy_assignments = _assign_components(xray_records, reference_records)
        selected_assignments = [
            {
                "xrayComponentIndex": int(xray_index),
                "referenceComponentIndex": int(reference_index),
                "assignmentCost": float(assignment_cost),
                "alignmentUtility": None,
                "alignment": None,
                "transform": None,
                "eligible": True,
                "rejectionReasons": [],
            }
            for xray_index, reference_index, assignment_cost in legacy_assignments
        ]
        candidate_summaries = {index: [] for index in range(len(bundles))}
        assignment_debug = {
            "mode": "legacy_greedy_shape_cost_assignment",
            "selectedPairCount": int(len(selected_assignments)),
        }
    final_poses: list[np.ndarray | None] = [None] * len(fragments)
    component_reports: list[dict[str, Any]] = []
    assigned_xray: set[int] = set()
    assigned_details: dict[int, dict[str, Any]] = {}
    bundle_series_keys = [
        {_scanner_series_key(fragments[index]) for index in bundle["indices"]}
        for bundle in bundles
    ]
    for candidate in selected_assignments:
        xray_index = int(candidate["xrayComponentIndex"])
        reference_index = int(candidate["referenceComponentIndex"])
        assignment_cost = float(candidate["assignmentCost"])
        bundle = bundles[xray_index]
        reference_record = reference_records[reference_index]
        component_transform = candidate.get("transform")
        component_score = candidate.get("alignment")
        if component_transform is None or component_score is None:
            component_transform, component_score = _align_component_mask_to_target(
                bundle["mask"], reference_record["mask"], config
            )
        for fragment_index in bundle["indices"]:
            final_poses[fragment_index] = (
                component_transform @ bundle["poses"][fragment_index]
            )
        assigned_xray.add(xray_index)
        placed_mask = cv2.warpAffine(
            (bundle["mask"] > 0).astype(np.uint8) * 255,
            component_transform[:2].astype(np.float32),
            (canvas_w, canvas_h),
            flags=cv2.INTER_NEAREST,
        )
        assigned_details[xray_index] = {
            "referenceIndex": reference_index,
            "transform": component_transform,
            "placedMask": placed_mask,
        }
        component_reports.append(
            {
                "xrayComponentIndex": xray_index,
                "referenceComponentIndex": reference_index,
                "fragmentIndices": bundle["indices"],
                "assignmentCost": assignment_cost,
                "alignmentUtility": candidate.get("alignmentUtility"),
                "alignment": component_score,
                "status": "assigned_to_reference_component",
                "candidateReferenceComponentAssignments": candidate_summaries.get(
                    xray_index, []
                ),
                "reviewRequired": False,
            }
        )

    # Preserve every source image even when component counts differ.  A split
    # component from the same explicit acquisition series as an already
    # assigned component is placed near that component's color reference for
    # HITL correction instead of being parked far outside the target.  It is
    # deliberately kept as a separate, unresolved component: the filename stem
    # is auxiliary evidence for a useful initial location, not sufficient
    # evidence to merge registration components.
    if conservative_assignment:
        parking_x = 20.0
        parking_y = float(canvas_h + 20)
        parking_row_height = 0
        parking_row_limit = max(
            canvas_w,
            max((bundle["mask"].shape[1] + 40 for bundle in bundles), default=canvas_w),
        )
    else:
        parking_x = float(canvas_w + 20)
        parking_y = 20.0
        parking_row_height = 0
        parking_row_limit = canvas_w
    maximum_parking_x = canvas_w
    maximum_parking_y = canvas_h
    for xray_index, bundle in enumerate(bundles):
        if xray_index in assigned_xray:
            continue

        summaries = candidate_summaries.get(xray_index, [])
        eligible_summaries = [item for item in summaries if bool(item.get("eligible"))]
        if conservative_assignment:
            if not summaries:
                unassigned_reason = "no_reference_component_candidate"
                unassigned_reason_details: list[str] = []
            elif not eligible_summaries:
                unassigned_reason = "low_confidence_or_failed_rigid_alignment"
                unassigned_reason_details = sorted(
                    {
                        reason
                        for item in summaries
                        for reason in item.get("rejectionReasons", [])
                    }
                )
            else:
                unassigned_reason = "global_one_to_one_conflict"
                unassigned_reason_details = []
        else:
            unassigned_reason = "component_count_mismatch"
            unassigned_reason_details = []

        series_keys = bundle_series_keys[xray_index]
        same_series_candidates: list[
            tuple[float, int, int, np.ndarray, dict[str, float], float]
        ] = []
        for linked_xray_index, details in assigned_details.items():
            shared_keys = series_keys & bundle_series_keys[linked_xray_index]
            if not shared_keys:
                continue
            reference_index = int(details["referenceIndex"])
            reference_record = reference_records[reference_index]
            component_transform, component_score = _align_component_mask_to_target(
                bundle["mask"], reference_record["mask"], config
            )
            placed_mask = cv2.warpAffine(
                (bundle["mask"] > 0).astype(np.uint8) * 255,
                component_transform[:2].astype(np.float32),
                (canvas_w, canvas_h),
                flags=cv2.INTER_NEAREST,
            )
            component_pixels = max(int(np.count_nonzero(placed_mask)), 1)
            inside_ratio = float(
                np.count_nonzero((placed_mask > 0) & (reference_record["mask"] > 0))
            ) / component_pixels
            linked_overlap_ratio = float(
                np.count_nonzero((placed_mask > 0) & (details["placedMask"] > 0))
            ) / component_pixels
            objective = (
                float(component_score.get("score", 0.0))
                + 0.18 * inside_ratio
                - 0.06 * linked_overlap_ratio
            )
            same_series_candidates.append(
                (
                    objective,
                    linked_xray_index,
                    reference_index,
                    component_transform,
                    component_score,
                    inside_ratio,
                )
            )

        # Legacy behavior is retained for rollback.  Conservative mode always
        # parks unresolved components below the reference instead of presenting
        # a low-confidence near-reference guess as if it were a useful placement.
        if same_series_candidates and not conservative_assignment:
            (
                _,
                linked_xray_index,
                reference_index,
                transform,
                component_score,
                inside_ratio,
            ) = max(same_series_candidates, key=lambda item: item[0])
            if inside_ratio >= 0.55:
                for fragment_index in bundle["indices"]:
                    final_poses[fragment_index] = (
                        transform @ bundle["poses"][fragment_index]
                    )
                component_reports.append(
                    {
                        "xrayComponentIndex": xray_index,
                        "referenceComponentIndex": reference_index,
                        "fragmentIndices": bundle["indices"],
                        "assignmentCost": None,
                        "alignment": component_score,
                        "status": "unassigned_same_series_placed_near_reference_for_hitl",
                        "linkedAssignedXrayComponentIndex": linked_xray_index,
                        "sharedSeriesKeys": sorted(
                            series_keys & bundle_series_keys[linked_xray_index]
                        ),
                        "insideReferenceRatio": inside_ratio,
                        "unassignedReason": unassigned_reason,
                        "unassignedReasonDetails": unassigned_reason_details,
                        "candidateReferenceComponentAssignments": summaries,
                        "reviewRequired": True,
                    }
                )
                continue

        bundle_height, bundle_width = bundle["mask"].shape
        if conservative_assignment:
            if (
                parking_x > 20.0
                and parking_x + bundle_width + 20.0 > parking_row_limit
            ):
                parking_x = 20.0
                parking_y += float(parking_row_height + 20)
                parking_row_height = 0
            transform = _translation(parking_x, parking_y)
            parking_x += float(bundle_width + 20)
            parking_row_height = max(parking_row_height, bundle_height)
            maximum_parking_x = max(
                maximum_parking_x, int(math.ceil(parking_x + 20))
            )
            maximum_parking_y = max(
                maximum_parking_y,
                int(math.ceil(parking_y + parking_row_height + 20)),
            )
        else:
            transform = _translation(parking_x, parking_y)
            parking_y += float(bundle_height + 20)
            maximum_parking_x = max(
                maximum_parking_x, canvas_w + bundle_width + 40
            )
            maximum_parking_y = max(maximum_parking_y, int(math.ceil(parking_y)))
        for fragment_index in bundle["indices"]:
            final_poses[fragment_index] = transform @ bundle["poses"][fragment_index]
        component_reports.append(
            {
                "xrayComponentIndex": xray_index,
                "referenceComponentIndex": None,
                "fragmentIndices": bundle["indices"],
                "assignmentCost": None,
                "alignment": None,
                "status": "unassigned_parked_for_hitl",
                "unassignedReason": unassigned_reason,
                "unassignedReasonDetails": unassigned_reason_details,
                "candidateReferenceComponentAssignments": summaries,
                "reviewRequired": True,
            }
        )

    extra_width = max(0, int(maximum_parking_x - canvas_w))
    extra_height = max(0, int(maximum_parking_y - canvas_h))
    if extra_width or extra_height:
        canvas_w += extra_width
        canvas_h += extra_height
        target = cv2.copyMakeBorder(
            target,
            0,
            extra_height,
            0,
            extra_width,
            cv2.BORDER_CONSTANT,
            value=0,
        )
        preview = cv2.copyMakeBorder(
            preview,
            0,
            extra_height,
            0,
            extra_width,
            cv2.BORDER_CONSTANT,
            value=(0, 0, 0),
        )

    resolved_poses = [pose if pose is not None else np.eye(3, dtype=np.float64) for pose in final_poses]
    final_mosaic, final_mask, final_counts, _ = render_mosaic(
        fragments, resolved_poses, (canvas_w, canvas_h)
    )
    intersection = int(np.count_nonzero((final_mask > 0) & (target > 0)))
    union = int(np.count_nonzero((final_mask > 0) | (target > 0)))
    global_iou = intersection / max(union, 1)
    global_boundary = _boundary_f1(final_mask, target, tolerance=max(1, int(config.boundary_tolerance_px)))

    search_ratio = min(1.0, config.search_max_dimension / max(canvas_w, canvas_h))
    search_size = (
        max(32, int(round(canvas_w * search_ratio))),
        max(32, int(round(canvas_h * search_ratio))),
    )
    geometry = SearchGeometry(
        target_mask_full=target,
        target_preview_full=preview,
        target_mask_opt=cv2.resize(target, search_size, interpolation=cv2.INTER_NEAREST),
        search_scale_x=search_size[0] / canvas_w,
        search_scale_y=search_size[1] / canvas_h,
        canvas_width_full=canvas_w,
        canvas_height_full=canvas_h,
        reference_scale=reference_scale,
        target_offset_xy=(target_x, target_y),
    )
    report = {
        "score": float(0.82 * global_iou + 0.18 * global_boundary),
        "iou": float(global_iou),
        "boundaryF1": float(global_boundary),
        "xrayComponentCount": len(bundles),
        "referenceComponentCount": len(reference_records),
        "assignedComponentCount": len(assigned_xray),
        "unassignedComponentCount": max(0, len(bundles) - len(assigned_xray)),
        "assignmentMode": assignment_debug.get("mode"),
        "assignmentDebug": assignment_debug,
        "components": component_reports,
    }
    return resolved_poses, geometry, report, final_mosaic, final_mask, final_counts

def align_mosaic_to_reference(
    reference_image: np.ndarray,
    reference_mask: np.ndarray,
    mosaic_mask: np.ndarray,
    config: AssemblyConfig,
) -> tuple[np.ndarray, SearchGeometry, dict[str, float]]:
    reference_area = int(np.count_nonzero(reference_mask))
    mosaic_area = int(np.count_nonzero(mosaic_mask))
    if reference_area <= 0 or mosaic_area <= 0:
        raise ValueError("reference 또는 X-ray mosaic mask 면적이 0입니다.")
    reference_scale = (
        float(config.reference_scale_override)
        if config.reference_scale_override is not None
        else math.sqrt(mosaic_area / max(reference_area * config.reference_fill_ratio, 1.0))
    )
    reference_h, reference_w = reference_mask.shape
    scaled_w = max(1, int(round(reference_w * reference_scale)))
    scaled_h = max(1, int(round(reference_h * reference_scale)))
    scaled_reference = cv2.resize(reference_mask, (scaled_w, scaled_h), interpolation=cv2.INTER_NEAREST)
    scaled_preview = cv2.resize(
        normalize_preview(reference_image),
        (scaled_w, scaled_h),
        interpolation=cv2.INTER_AREA if reference_scale < 1.0 else cv2.INTER_LINEAR,
    )

    mosaic_h, mosaic_w = mosaic_mask.shape
    diagonal = int(math.ceil(math.hypot(mosaic_w, mosaic_h)))
    margin = max(20, int(round(max(scaled_w, scaled_h, diagonal) * config.canvas_margin_ratio)))
    canvas_w = max(scaled_w, diagonal) + 2 * margin
    canvas_h = max(scaled_h, diagonal) + 2 * margin
    target = np.zeros((canvas_h, canvas_w), dtype=np.uint8)
    preview = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    target_x = (canvas_w - scaled_w) // 2
    target_y = (canvas_h - scaled_h) // 2
    target[target_y : target_y + scaled_h, target_x : target_x + scaled_w] = scaled_reference
    preview[target_y : target_y + scaled_h, target_x : target_x + scaled_w] = scaled_preview

    base = _normalize_angle(principal_angle_deg(target) - principal_angle_deg(mosaic_mask))
    coarse_angles: set[float] = {0.0, 180.0, base, _normalize_angle(base + 180.0)}
    for seed in list(coarse_angles):
        for offset in range(-config.global_angle_radius_deg, config.global_angle_radius_deg + 1, config.global_angle_step_deg):
            coarse_angles.add(round(_normalize_angle(seed + offset), 6))
    if config.global_full_rotation_step_deg > 0:
        for angle in range(-180, 180, config.global_full_rotation_step_deg):
            coarse_angles.add(float(angle))

    def evaluate(angle: float) -> tuple[float, np.ndarray, float, float]:
        rotation, rotated_size = _rotation_bound_matrix(mosaic_w, mosaic_h, angle)
        rotated = cv2.warpAffine(
            mosaic_mask,
            rotation[:2].astype(np.float32),
            rotated_size,
            flags=cv2.INTER_NEAREST,
        )
        rotated_binary = (rotated > 0).astype(np.float32)
        target_float = (target > 0).astype(np.float32)
        if rotated.shape[0] > target.shape[0] or rotated.shape[1] > target.shape[1]:
            return -1.0, np.eye(3), 0.0, 0.0
        response = cv2.matchTemplate(target_float, rotated_binary, cv2.TM_CCORR)
        _, maximum, _, location = cv2.minMaxLoc(response)
        placement = _translation(float(location[0]), float(location[1])) @ rotation
        placed = cv2.warpAffine(
            mosaic_mask,
            placement[:2].astype(np.float32),
            (canvas_w, canvas_h),
            flags=cv2.INTER_NEAREST,
        )
        intersection = int(np.count_nonzero((placed > 0) & (target > 0)))
        union = int(np.count_nonzero((placed > 0) | (target > 0)))
        iou = intersection / max(union, 1)
        boundary = _boundary_f1(placed, target, tolerance=max(1, int(config.boundary_tolerance_px)))
        score = 0.82 * iou + 0.18 * boundary
        return float(score), placement, float(iou), float(boundary)

    best = max((evaluate(angle) + (angle,) for angle in sorted(coarse_angles)), key=lambda item: item[0])
    best_score, best_transform, best_iou, best_boundary, best_angle = best
    fine_angles = [best_angle + offset for offset in np.arange(-config.global_angle_step_deg, config.global_angle_step_deg + 0.01, config.global_refine_angle_step_deg)]
    best = max((evaluate(float(angle)) + (float(angle),) for angle in fine_angles), key=lambda item: item[0])
    best_score, best_transform, best_iou, best_boundary, best_angle = best

    search_ratio = min(1.0, config.search_max_dimension / max(canvas_w, canvas_h))
    search_size = (max(32, int(round(canvas_w * search_ratio))), max(32, int(round(canvas_h * search_ratio))))
    geometry = SearchGeometry(
        target_mask_full=target,
        target_preview_full=preview,
        target_mask_opt=cv2.resize(target, search_size, interpolation=cv2.INTER_NEAREST),
        search_scale_x=search_size[0] / canvas_w,
        search_scale_y=search_size[1] / canvas_h,
        canvas_width_full=canvas_w,
        canvas_height_full=canvas_h,
        reference_scale=reference_scale,
        target_offset_xy=(target_x, target_y),
    )
    return best_transform, geometry, {
        "score": float(best_score),
        "iou": float(best_iou),
        "boundaryF1": float(best_boundary),
        "rotationDeg": float(_normalize_angle(best_angle)),
    }


def matrices_to_placements(fragments: list[Fragment], matrices: list[np.ndarray]) -> list[Placement]:
    placements: list[Placement] = []
    for fragment, matrix in zip(fragments, matrices):
        height, width = fragment.mask.shape
        center = _transform_points(matrix, np.array([[(width - 1) / 2.0, (height - 1) / 2.0]]))[0]
        angle = math.degrees(math.atan2(matrix[1, 0], matrix[0, 0]))
        placements.append(
            Placement(
                fragment_index=fragment.index,
                center_x=float(center[0]),
                center_y=float(center[1]),
                rotation_deg=float(_normalize_angle(angle)),
            )
        )
    return placements


def make_alignment_overlay(
    target_preview: np.ndarray,
    target_mask: np.ndarray,
    assembly_mask: np.ndarray,
    overlap_counts: np.ndarray,
) -> np.ndarray:
    overlay = target_preview.astype(np.float32) * 0.45
    target = target_mask > 0
    assembly = assembly_mask > 0
    overlay[target & assembly] = (40, 190, 40)
    overlay[target & ~assembly] = (220, 80, 40)
    overlay[assembly & ~target] = (40, 60, 220)
    overlay[overlap_counts > 1] = (220, 40, 220)
    return np.clip(overlay, 0, 255).astype(np.uint8)


def stitch_fragments(
    fragments: list[Fragment],
    reference_image: np.ndarray,
    reference_mask: np.ndarray,
    config: AssemblyConfig,
) -> dict[str, Any]:
    registrations, registration_debug = compute_pairwise_registrations(fragments, config)
    tree_edges, forest_debug = _maximum_spanning_forest(
        len(fragments), registrations, config, fragments
    )
    registration_debug["forestSelection"] = forest_debug
    raw_poses, components = _pose_components(len(fragments), tree_edges, fragments)
    raw_poses, pose_graph_debug = _refine_poses_with_pose_graph(
        raw_poses,
        components,
        registrations,
        fragments,
        config,
    )
    registration_debug["poseGraph"] = pose_graph_debug
    mosaic_poses, mosaic_size = normalize_poses_to_canvas(fragments, raw_poses, margin=20)
    initial_mosaic, initial_mask, initial_counts, _ = render_mosaic(fragments, mosaic_poses, mosaic_size)
    final_poses, geometry, alignment_score, final_mosaic, final_mask, final_counts = align_components_to_reference(
        reference_image,
        reference_mask,
        fragments,
        raw_poses,
        components,
        config,
    )
    return {
        "registrations": registrations,
        "registrationDebug": registration_debug,
        "treeEdges": tree_edges,
        "components": components,
        "mosaicPoses": mosaic_poses,
        "initialMosaic": initial_mosaic,
        "initialMask": initial_mask,
        "initialCounts": initial_counts,
        "globalTransform": np.eye(3, dtype=np.float64),
        "geometry": geometry,
        "alignmentScore": alignment_score,
        "finalPoses": final_poses,
        "finalMosaic": final_mosaic,
        "finalMask": final_mask,
        "finalCounts": final_counts,
        "owner": None,
        "placements": matrices_to_placements(fragments, final_poses),
    }

