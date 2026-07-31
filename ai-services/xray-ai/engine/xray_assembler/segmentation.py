from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .config import AssemblyConfig
from .image_ops import (
    clean_mask,
    crop_to_mask,
    iter_border_pixels,
    principal_angle_deg,
    read_image,
    to_gray_for_analysis,
    to_uint8_for_analysis,
)
from .models import Fragment


def _mask_quality(mask: np.ndarray) -> float:
    binary = mask > 0
    area_ratio = float(binary.mean())
    if area_ratio <= 0.001 or area_ratio >= 0.98:
        return -1e9
    border = np.concatenate(
        [binary[0, :], binary[-1, :], binary[:, 0], binary[:, -1]]
    )
    border_ratio = float(border.mean())
    center = binary[
        binary.shape[0] // 4 : 3 * binary.shape[0] // 4,
        binary.shape[1] // 4 : 3 * binary.shape[1] // 4,
    ]
    center_ratio = float(center.mean()) if center.size else 0.0
    area_preference = -abs(area_ratio - 0.35)
    return 2.0 * area_preference + 0.7 * center_ratio - 2.5 * border_ratio


def _clean_mask_preserve_holes(
    mask: np.ndarray,
    kernel_size: int = 3,
    min_area_ratio: float = 0.002,
    keep_largest_only: bool = False,
) -> np.ndarray:
    binary = (mask > 0).astype(np.uint8) * 255
    kernel_size = max(1, int(kernel_size))
    if kernel_size % 2 == 0:
        kernel_size += 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    count, labels, stats, _ = cv2.connectedComponentsWithStats((binary > 0).astype(np.uint8), connectivity=8)
    if count <= 1:
        return binary
    areas = stats[1:, cv2.CC_STAT_AREA]
    largest = int(areas.max())
    output = np.zeros_like(binary)
    for label, area in enumerate(areas, start=1):
        if keep_largest_only:
            keep = int(area) == largest
        else:
            keep = int(area) >= max(8, int(round(largest * min_area_ratio)))
        if keep:
            output[labels == label] = 255
    return output


def _fragment_mask_quality(mask: np.ndarray, gray: np.ndarray) -> float:
    binary = mask > 0
    area_ratio = float(binary.mean())
    if area_ratio <= 0.003 or area_ratio >= 0.93:
        return -1e9
    foreground = gray[binary]
    background = gray[~binary]
    if foreground.size < 16 or background.size < 16:
        return -1e9
    separation = abs(float(foreground.mean()) - float(background.mean())) / 64.0
    bright_preference = (float(foreground.mean()) - float(background.mean())) / 96.0
    count, _, stats, _ = cv2.connectedComponentsWithStats(binary.astype(np.uint8), connectivity=8)
    concentration = 0.0
    if count > 1:
        concentration = float(stats[1:, cv2.CC_STAT_AREA].max()) / max(int(binary.sum()), 1)
    return separation + 0.35 * bright_preference + 0.55 * concentration - 0.25 * abs(area_ratio - 0.35)


def _resize_for_reference_analysis(
    image: np.ndarray, max_dimension: int
) -> tuple[np.ndarray, float]:
    h, w = image.shape[:2]
    if max_dimension <= 0 or max(h, w) <= max_dimension:
        return image, 1.0
    scale = float(max_dimension) / float(max(h, w))
    resized = cv2.resize(
        image,
        (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
        interpolation=cv2.INTER_AREA,
    )
    return resized, scale


def _restore_reference_mask(mask: np.ndarray, shape_hw: tuple[int, int]) -> np.ndarray:
    h, w = shape_hw
    if mask.shape == (h, w):
        return mask
    return cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)


def _remove_probable_scale_bar(
    mask: np.ndarray, bgr: np.ndarray, config: AssemblyConfig
) -> np.ndarray:
    if not config.reference_remove_scale_bar:
        return mask
    binary = (mask > 0).astype(np.uint8)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if num <= 1:
        return mask
    hsv = cv2.cvtColor(to_uint8_for_analysis(bgr), cv2.COLOR_BGR2HSV)
    h, w = mask.shape
    candidates: list[dict[str, float | int | np.ndarray]] = []
    for label in range(1, num):
        x, y, cw, ch, area = [int(v) for v in stats[label]]
        if area <= 0:
            continue
        center_y_ratio = (y + 0.5 * ch) / max(h, 1)
        height_ratio = ch / max(h, 1)
        width_ratio = cw / max(w, 1)
        selected = labels == label
        mean_saturation = float(np.mean(hsv[:, :, 1][selected])) if np.any(selected) else 255.0
        aspect = cw / max(ch, 1)
        if (
            center_y_ratio >= config.reference_scale_bar_min_y_ratio
            and height_ratio <= config.reference_scale_bar_max_height_ratio
            and config.reference_scale_bar_min_width_ratio <= width_ratio <= config.reference_scale_bar_max_width_ratio
        ):
            candidates.append(
                {
                    "label": label,
                    "center_y": y + 0.5 * ch,
                    "width_ratio": width_ratio,
                    "aspect": aspect,
                    "selected": selected,
                }
            )
    if not candidates:
        return mask

    # Scale bars usually consist of two or more low-saturation blocks aligned
    # on the same row. A single wide horizontal component is also accepted.
    remove_labels: set[int] = set()
    tolerance = 0.025 * h
    for candidate in candidates:
        aligned = [
            other
            for other in candidates
            if abs(float(other["center_y"]) - float(candidate["center_y"])) <= tolerance
        ]
        if len(aligned) >= 2 or (
            float(candidate["width_ratio"]) >= 0.04
            and float(candidate["aspect"]) >= 1.8
        ):
            remove_labels.update(int(item["label"]) for item in aligned)

    result = mask.copy()
    for label in remove_labels:
        result[labels == label] = 0
    return result


def _apply_reference_exclusions(
    mask: np.ndarray, config: AssemblyConfig, bgr: np.ndarray | None = None
) -> np.ndarray:
    result = mask.copy()
    ignore_bottom = float(np.clip(config.reference_ignore_bottom_ratio, 0.0, 0.49))
    if ignore_bottom > 0:
        h = result.shape[0]
        y0 = int(round(h * (1.0 - ignore_bottom)))
        result[max(0, min(h, y0)) :, :] = 0
    result = _clean_mask_preserve_holes(
        result, kernel_size=5, min_area_ratio=config.min_component_area_ratio
    )
    if bgr is not None:
        result = _remove_probable_scale_bar(result, bgr, config)
    # Keep all artifact components. Some completed references intentionally show
    # detached physical pieces in their final relative positions.
    return _clean_mask_preserve_holes(
        result, kernel_size=3, min_area_ratio=config.min_component_area_ratio, keep_largest_only=False
    )


def segment_reference_border_distance(image: np.ndarray, config: AssemblyConfig) -> np.ndarray:
    if image.ndim == 2:
        bgr = cv2.cvtColor(to_uint8_for_analysis(image), cv2.COLOR_GRAY2BGR)
    elif image.ndim == 3 and image.shape[2] == 4:
        bgr = to_uint8_for_analysis(image[:, :, :3])
    else:
        bgr = to_uint8_for_analysis(image)

    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    h, w = lab.shape[:2]
    bw = max(2, int(round(min(h, w) * config.reference_margin_ratio)))
    border = np.concatenate(
        [
            lab[:bw, :, :].reshape(-1, 3),
            lab[-bw:, :, :].reshape(-1, 3),
            lab[:, :bw, :].reshape(-1, 3),
            lab[:, -bw:, :].reshape(-1, 3),
        ],
        axis=0,
    )
    bg = np.median(border, axis=0)
    dist = np.linalg.norm(lab - bg[None, None, :], axis=2)
    dist8 = to_uint8_for_analysis(dist)
    _, raw = cv2.threshold(dist8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return _apply_reference_exclusions(raw, config, bgr)


def segment_reference_grabcut(image: np.ndarray, config: AssemblyConfig) -> np.ndarray:
    if image.ndim == 2:
        bgr = cv2.cvtColor(to_uint8_for_analysis(image), cv2.COLOR_GRAY2BGR)
    elif image.ndim == 3 and image.shape[2] == 4:
        bgr = to_uint8_for_analysis(image[:, :, :3])
    else:
        bgr = to_uint8_for_analysis(image)

    h, w = bgr.shape[:2]
    margin_x = max(1, int(round(w * config.reference_margin_ratio)))
    margin_y = max(1, int(round(h * config.reference_margin_ratio)))
    rect = (
        margin_x,
        margin_y,
        max(1, w - 2 * margin_x),
        max(1, h - 2 * margin_y),
    )
    gc_mask = np.zeros((h, w), np.uint8)
    bg_model = np.zeros((1, 65), np.float64)
    fg_model = np.zeros((1, 65), np.float64)
    cv2.grabCut(bgr, gc_mask, rect, bg_model, fg_model, 5, cv2.GC_INIT_WITH_RECT)
    raw = np.where(
        (gc_mask == cv2.GC_FGD) | (gc_mask == cv2.GC_PR_FGD), 255, 0
    ).astype(np.uint8)
    return _apply_reference_exclusions(raw, config, bgr)


def segment_reference(
    image: np.ndarray,
    config: AssemblyConfig,
    explicit_mask_path: str | Path | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    if explicit_mask_path is not None:
        mask_img = read_image(explicit_mask_path)
        gray = to_gray_for_analysis(mask_img)
        mask = _clean_mask_preserve_holes(
            gray, kernel_size=3, min_area_ratio=config.min_component_area_ratio, keep_largest_only=False
        )
        if mask.shape != image.shape[:2]:
            raise ValueError(
                f"reference mask shape {mask.shape} != reference image shape {image.shape[:2]}"
            )
        return mask, {
            "method": "explicit_mask",
            "quality": _mask_quality(mask),
            "analysisScale": 1.0,
            "ignoreBottomRatio": config.reference_ignore_bottom_ratio,
        }

    analysis_image, scale = _resize_for_reference_analysis(
        image, int(config.reference_analysis_max_dimension)
    )
    mode = config.reference_segmentation.lower()
    candidates: list[tuple[str, np.ndarray]] = []
    if mode in {"auto", "grabcut"}:
        candidates.append(("grabcut", segment_reference_grabcut(analysis_image, config)))
    if mode in {"auto", "border_distance"}:
        candidates.append(
            (
                "border_distance",
                segment_reference_border_distance(analysis_image, config),
            )
        )
    if not candidates:
        raise ValueError(f"지원하지 않는 reference_segmentation: {mode}")

    scored = [(name, mask, _mask_quality(mask)) for name, mask in candidates]
    name, selected_small, quality = max(scored, key=lambda item: item[2])
    selected = _restore_reference_mask(selected_small, image.shape[:2])
    selected = _clean_mask_preserve_holes(
        selected, kernel_size=3, min_area_ratio=config.min_component_area_ratio, keep_largest_only=False
    )
    return selected, {
        "method": name,
        "quality": float(quality),
        "analysisScale": float(scale),
        "analysisShape": list(analysis_image.shape[:2]),
        "originalShape": list(image.shape[:2]),
        "ignoreBottomRatio": float(config.reference_ignore_bottom_ratio),
        "candidates": {n: float(q) for n, _, q in scored},
    }


def segment_fragment_mask(
    image: np.ndarray,
    config: AssemblyConfig,
    explicit_mask_path: str | Path | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    if explicit_mask_path is not None:
        raw_mask = read_image(explicit_mask_path)
        gray_mask = to_gray_for_analysis(raw_mask)
        mask = _clean_mask_preserve_holes(
            gray_mask, kernel_size=config.fragment_morph_kernel, min_area_ratio=config.min_component_area_ratio
        )
        if mask.shape != image.shape[:2]:
            raise ValueError(
                f"fragment mask shape {mask.shape} != fragment image shape {image.shape[:2]}"
            )
        return mask, {"method": "explicit_mask", "area_ratio": float(np.mean(mask > 0))}

    if image.ndim == 3 and image.shape[2] == 4:
        alpha = image[:, :, 3]
        if int(alpha.max()) > int(alpha.min()):
            mask = _clean_mask_preserve_holes(
                alpha, kernel_size=config.fragment_morph_kernel, min_area_ratio=config.min_component_area_ratio
            )
            if np.count_nonzero(mask) > 0:
                return mask, {"method": "alpha", "area_ratio": float(np.mean(mask > 0))}

    gray = to_uint8_for_analysis(to_gray_for_analysis(image))
    h, w = gray.shape
    border_width = max(2, int(round(min(h, w) * config.fragment_border_width_ratio)))
    border = iter_border_pixels(gray, border_width)
    background_median = float(np.median(border))
    robust_sigma = float(np.median(np.abs(border.astype(np.float32) - background_median)) * 1.4826)
    delta = max(8.0, 2.5 * robust_sigma)

    _, otsu_bright = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, otsu_dark = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    bright_distance = (gray.astype(np.float32) > background_median + delta).astype(np.uint8) * 255
    dark_distance = (gray.astype(np.float32) < background_median - delta).astype(np.uint8) * 255
    candidates = {
        "otsu_bright": otsu_bright,
        "otsu_dark": otsu_dark,
        "border_bright": bright_distance,
        "border_dark": dark_distance,
    }
    # The source X-ray files contain a thin bright scanner frame. It must not
    # become connected to an artifact that legitimately reaches an image edge.
    frame = max(0, int(config.fragment_frame_border_px))
    if frame > 0:
        for candidate in candidates.values():
            candidate[:frame, :] = 0
            candidate[-frame:, :] = 0
            candidate[:, :frame] = 0
            candidate[:, -frame:] = 0
    cleaned = {
        name: _clean_mask_preserve_holes(
            candidate, kernel_size=config.fragment_morph_kernel, min_area_ratio=config.min_component_area_ratio
        )
        for name, candidate in candidates.items()
    }
    qualities = {name: _fragment_mask_quality(mask, gray) for name, mask in cleaned.items()}
    method = max(qualities, key=qualities.get)
    mask = cleaned[method]
    area_ratio = float(np.mean(mask > 0))
    if np.count_nonzero(mask) == 0 or area_ratio >= 0.93:
        raise ValueError(
            f"신뢰할 수 있는 X-ray foreground mask를 만들지 못했습니다: method={method}, area_ratio={area_ratio:.4f}"
        )
    touch_strip = max(frame + 3, 6)
    binary = mask > 0
    touch_counts = {
        "top": int(np.count_nonzero(binary[:touch_strip, :])),
        "bottom": int(np.count_nonzero(binary[-touch_strip:, :])),
        "left": int(np.count_nonzero(binary[:, :touch_strip])),
        "right": int(np.count_nonzero(binary[:, -touch_strip:])),
    }
    touch_threshold = max(4, int(round(0.01 * min(h, w))))
    touches = {name: count >= touch_threshold for name, count in touch_counts.items()}
    return mask, {
        "method": method,
        "background_median": background_median,
        "background_robust_sigma": robust_sigma,
        "area_ratio": area_ratio,
        "candidate_quality": qualities,
        "touches_frame": any(touches.values()),
        "touch_sides": touches,
        "touch_counts": touch_counts,
    }


def _dominant_fragment_polarity(diagnostics: list[dict[str, Any]], config: AssemblyConfig) -> str | None:
    bright_votes = 0
    dark_votes = 0
    for item in diagnostics:
        method = str(item.get("method", ""))
        area_ratio = float(item.get("area_ratio", 0.0))
        # Extremely small or nearly full masks are exactly the ambiguous cases
        # this vote is intended to correct, so they do not influence polarity.
        if not (0.01 <= area_ratio <= 0.90):
            continue
        if method.endswith("bright"):
            bright_votes += 1
        elif method.endswith("dark"):
            dark_votes += 1
    minimum = max(1, int(config.fragment_frame_fill_min_polarity_votes))
    if bright_votes >= minimum and bright_votes > dark_votes:
        return "bright"
    if dark_votes >= minimum and dark_votes > bright_votes:
        return "dark"
    return None


def _full_frame_mask(shape: tuple[int, int], config: AssemblyConfig) -> np.ndarray:
    height, width = shape
    mask = np.full((height, width), 255, dtype=np.uint8)
    frame = max(0, int(config.fragment_frame_border_px))
    if frame > 0 and height > 2 * frame and width > 2 * frame:
        mask[:frame, :] = 0
        mask[-frame:, :] = 0
        mask[:, :frame] = 0
        mask[:, -frame:] = 0
    return mask


def _component_frame_contact(
    mask: np.ndarray,
    bbox_xywh: tuple[int, int, int, int],
    source_shape: tuple[int, int] | list[int],
    config: AssemblyConfig,
) -> dict[str, Any]:
    """Classify one cropped foreground component against the original frame.

    The mask itself is cropped, so frame contact must be evaluated after mapping
    its foreground pixels back to the unmodified source-image coordinates.  This
    keeps an enclosed object independent even when another object in the same
    source frame reaches the acquisition boundary.
    """
    source_height, source_width = int(source_shape[0]), int(source_shape[1])
    x, y, _, _ = [int(value) for value in bbox_xywh]
    local_y, local_x = np.nonzero(mask > 0)
    touch_strip = max(int(config.fragment_frame_border_px) + 3, 6)
    if local_x.size == 0:
        touch_counts = {"top": 0, "bottom": 0, "left": 0, "right": 0}
    else:
        global_x = local_x.astype(np.int64) + x
        global_y = local_y.astype(np.int64) + y
        touch_counts = {
            "top": int(np.count_nonzero(global_y < touch_strip)),
            "bottom": int(
                np.count_nonzero(global_y >= max(source_height - touch_strip, 0))
            ),
            "left": int(np.count_nonzero(global_x < touch_strip)),
            "right": int(
                np.count_nonzero(global_x >= max(source_width - touch_strip, 0))
            ),
        }
    touch_threshold = max(4, int(round(0.01 * min(source_height, source_width))))
    touch_sides = {
        name: count >= touch_threshold for name, count in touch_counts.items()
    }
    touches_frame = bool(any(touch_sides.values()))
    return {
        "component_touches_frame": touches_frame,
        "component_touch_sides": touch_sides,
        "component_touch_counts": touch_counts,
        "component_touch_strip_px": int(touch_strip),
        "component_touch_threshold_px": int(touch_threshold),
        "capture_role": (
            "partial_capture_candidate"
            if touches_frame
            else "independent_fragment_candidate"
        ),
    }


def _frame_fill_override(
    image: np.ndarray,
    initial_mask: np.ndarray,
    initial_diagnostics: dict[str, Any],
    dominant_polarity: str | None,
    config: AssemblyConfig,
) -> tuple[np.ndarray, dict[str, Any]]:
    if not bool(config.fragment_frame_fill_enabled) or dominant_polarity is None:
        return initial_mask, initial_diagnostics

    gray = to_uint8_for_analysis(to_gray_for_analysis(image))
    if dominant_polarity == "bright":
        fill_fraction = float(np.mean(gray >= int(config.fragment_frame_fill_bright_threshold)))
    else:
        fill_fraction = float(np.mean(gray <= int(config.fragment_frame_fill_dark_threshold)))
    if fill_fraction < float(config.fragment_frame_fill_fraction):
        return initial_mask, initial_diagnostics

    mask = _full_frame_mask(gray.shape, config)
    binary = mask > 0
    touch_strip = max(int(config.fragment_frame_border_px) + 3, 6)
    touch_counts = {
        "top": int(np.count_nonzero(binary[:touch_strip, :])),
        "bottom": int(np.count_nonzero(binary[-touch_strip:, :])),
        "left": int(np.count_nonzero(binary[:, :touch_strip])),
        "right": int(np.count_nonzero(binary[:, -touch_strip:])),
    }
    diagnostics = dict(initial_diagnostics)
    diagnostics.update(
        {
            "method": f"frame_filled_{dominant_polarity}_sequence",
            "area_ratio": float(np.mean(binary)),
            "frame_fill_fraction": fill_fraction,
            "dominant_sequence_polarity": dominant_polarity,
            "touches_frame": True,
            "touch_sides": {name: True for name in touch_counts},
            "touch_counts": touch_counts,
            "initial_method": initial_diagnostics.get("method"),
            "initial_area_ratio": initial_diagnostics.get("area_ratio"),
        }
    )
    return mask, diagnostics


def split_multi_object_fragments(
    fragments: list[Fragment], config: AssemblyConfig
) -> list[Fragment]:
    if not bool(config.fragment_array_split_multi_object_sources):
        return fragments
    split_fragments: list[Fragment] = []
    next_index = 0
    for fragment in fragments:
        binary = (fragment.mask > 0).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
        source_foreground_area = int(np.count_nonzero(fragment.mask))
        if count <= 2:
            fragment.diagnostics = dict(fragment.diagnostics)
            fragment.diagnostics.update(
                {
                    "sourceForegroundAreaPx": source_foreground_area,
                    "sourceSplitPreservedAreaPx": source_foreground_area,
                    "residualNoiseGroup": False,
                }
            )
            kept = [fragment]
        else:
            raw_areas = [int(value) for value in stats[1:, cv2.CC_STAT_AREA]]
            largest = max(raw_areas) if raw_areas else 0
            threshold = max(
                int(config.fragment_array_split_min_component_pixels),
                int(round(largest * float(config.fragment_array_split_min_component_area_ratio))),
            )
            large_labels = [
                label
                for label in range(1, count)
                if int(stats[label, cv2.CC_STAT_AREA]) >= threshold
            ]
            # One physical candidate plus tiny specks should remain one source
            # item. Splitting is useful only when the frame clearly contains at
            # least two substantive objects.
            if len(large_labels) <= 1:
                fragment.diagnostics = dict(fragment.diagnostics)
                fragment.diagnostics.update(
                    {
                        "sourceForegroundAreaPx": source_foreground_area,
                        "sourceSplitPreservedAreaPx": source_foreground_area,
                        "sourceSplitAreaThresholdPx": int(threshold),
                        "residualNoiseGroup": False,
                    }
                )
                kept = [fragment]
                for item in kept:
                    item.index = next_index
                    next_index += 1
                    split_fragments.append(item)
                continue

            parts: list[Fragment] = []
            component_groups: list[tuple[list[int], bool]] = [
                ([label], False) for label in large_labels
            ]
            residual_labels = [
                label for label in range(1, count) if label not in set(large_labels)
            ]
            if residual_labels:
                # Preserve every foreground pixel. Small disconnected specks are
                # kept as one review-only residual group rather than silently
                # dropped or counted as independent physical fragments.
                component_groups.append((residual_labels, True))

            for label_group, is_residual in component_groups:
                area = int(
                    sum(int(stats[label, cv2.CC_STAT_AREA]) for label in label_group)
                )
                component_mask = np.where(
                    np.isin(labels, np.asarray(label_group, dtype=labels.dtype)),
                    255,
                    0,
                ).astype(np.uint8)
                cropped_image, cropped_mask, local_bbox = crop_to_mask(
                    fragment.image, component_mask, padding=2
                )
                source_x, source_y, _, _ = fragment.crop_bbox_xywh
                lx, ly, lw, lh = local_bbox
                global_bbox = (source_x + lx, source_y + ly, lw, lh)
                part_index = len(parts)
                synthetic_name = (
                    f"{Path(fragment.name).stem}__"
                    f"{'residual' if is_residual else f'sub{part_index:02d}'}"
                    f"{Path(fragment.name).suffix}"
                )
                synthetic_path = fragment.path.parent / synthetic_name
                diagnostics = dict(fragment.diagnostics)
                diagnostics.update(
                    {
                        "splitFromMultiObjectSource": True,
                        "originalSourcePath": str(fragment.source_path or fragment.path),
                        "sourceImagePath": str(fragment.source_path or fragment.path),
                        "originalSourceName": fragment.source_name or fragment.name,
                        "originalSourceIndex": int(fragment.source_index if fragment.source_index is not None else fragment.index),
                        "subfragmentIndex": int(part_index),
                        "sourceMaskComponentCount": int(count - 1),
                        "sourceMaskComponentAreaPx": int(area),
                        "sourceSplitAreaThresholdPx": int(threshold),
                        "sourceForegroundAreaPx": source_foreground_area,
                        "residualNoiseGroup": bool(is_residual),
                        "residualConnectedComponentLabels": (
                            [int(label) for label in label_group] if is_residual else []
                        ),
                    }
                )
                source_shape = diagnostics.get("source_shape", fragment.mask.shape)
                component_contact = _component_frame_contact(
                    cropped_mask, global_bbox, source_shape, config
                )
                diagnostics.update(component_contact)
                diagnostics["touches_frame"] = bool(
                    component_contact["component_touches_frame"]
                )
                diagnostics["touch_sides"] = dict(
                    component_contact["component_touch_sides"]
                )
                diagnostics["touch_counts"] = dict(
                    component_contact["component_touch_counts"]
                )
                if is_residual:
                    diagnostics["capture_role"] = "residual_noise_group"
                parts.append(
                    Fragment(
                        index=-1,
                        path=synthetic_path,
                        name=synthetic_name,
                        image=cropped_image,
                        gray=to_gray_for_analysis(cropped_image),
                        mask=cropped_mask,
                        crop_bbox_xywh=global_bbox,
                        mask_area=int(np.count_nonzero(cropped_mask)),
                        principal_angle_deg=principal_angle_deg(cropped_mask),
                        diagnostics=diagnostics,
                        source_path=fragment.source_path or fragment.path,
                        source_name=fragment.source_name or fragment.name,
                        source_index=(fragment.source_index if fragment.source_index is not None else fragment.index),
                        subfragment_index=part_index,
                    )
                )
            preserved_area = sum(int(np.count_nonzero(part.mask)) for part in parts)
            if preserved_area != source_foreground_area:
                raise RuntimeError(
                    "파편 분리 중 source foreground 면적이 보존되지 않았습니다: "
                    f"{fragment.name} expected={source_foreground_area} actual={preserved_area}"
                )
            for part in parts:
                part.diagnostics["sourceSplitPreservedAreaPx"] = int(preserved_area)
            kept = parts
        for item in kept:
            item.index = next_index
            next_index += 1
            split_fragments.append(item)
    return split_fragments


def load_fragments(
    fragment_paths: list[Path],
    config: AssemblyConfig,
    masks_dir: str | Path | None = None,
) -> list[Fragment]:
    mask_root = Path(masks_dir) if masks_dir is not None else None
    # Keep only masks and diagnostics after the first pass. Holding every full
    # source image until polarity voting finishes can exhaust memory for large
    # cases such as an 85-frame fragment array. The second pass re-reads one
    # source at a time, crops it without resampling, and then releases the full
    # frame. This changes memory use only; output pixels and transforms remain
    # identical.
    prepared: list[tuple[int, Path, np.ndarray, dict[str, Any]]] = []
    for index, path in enumerate(fragment_paths):
        image = read_image(path)
        explicit_mask = None
        if mask_root is not None:
            candidates = [
                mask_root / f"{path.stem}.png",
                mask_root / f"{path.stem}_mask.png",
                mask_root / path.name,
            ]
            explicit_mask = next((p for p in candidates if p.exists()), None)
            if explicit_mask is None:
                raise FileNotFoundError(f"파편 마스크를 찾을 수 없습니다: {path.name}")

        mask, diagnostics = segment_fragment_mask(image, config, explicit_mask)
        prepared.append((index, path, mask, diagnostics))

    dominant_polarity = None
    if mask_root is None:
        dominant_polarity = _dominant_fragment_polarity(
            [item[3] for item in prepared], config
        )

    fragments: list[Fragment] = []
    for index, path, initial_mask, initial_diagnostics in prepared:
        image = read_image(path)
        mask, diagnostics = _frame_fill_override(
            image,
            initial_mask,
            initial_diagnostics,
            dominant_polarity,
            config,
        )
        diagnostics = dict(diagnostics)
        diagnostics["source_shape"] = list(image.shape[:2])
        diagnostics["sourceImagePath"] = str(path)
        diagnostics["source_touches_frame"] = bool(
            diagnostics.get("touches_frame", False)
        )
        diagnostics["source_touch_sides"] = dict(
            diagnostics.get("touch_sides", {})
        )
        diagnostics["source_touch_counts"] = dict(
            diagnostics.get("touch_counts", {})
        )
        if np.count_nonzero(mask) == 0:
            raise ValueError(f"파편 마스크가 비어 있습니다: {path}")
        cropped_image, cropped_mask, bbox = crop_to_mask(image, mask, padding=4)
        component_contact = _component_frame_contact(
            cropped_mask, bbox, image.shape[:2], config
        )
        diagnostics.update(component_contact)
        # Downstream registration historically reads these generic keys.  From
        # this point they intentionally describe the current placement unit,
        # while source_* retains the original full-frame diagnosis.
        diagnostics["touches_frame"] = bool(
            component_contact["component_touches_frame"]
        )
        diagnostics["touch_sides"] = dict(
            component_contact["component_touch_sides"]
        )
        diagnostics["touch_counts"] = dict(
            component_contact["component_touch_counts"]
        )
        cropped_gray = to_gray_for_analysis(cropped_image)
        fragments.append(
            Fragment(
                index=index,
                path=path,
                name=path.name,
                image=cropped_image,
                gray=cropped_gray,
                mask=cropped_mask,
                crop_bbox_xywh=bbox,
                mask_area=int(np.count_nonzero(cropped_mask)),
                principal_angle_deg=principal_angle_deg(cropped_mask),
                diagnostics=diagnostics,
                source_path=path,
                source_name=path.name,
                source_index=index,
                subfragment_index=0,
            )
        )
    return fragments



def _connected_component_specs(mask: np.ndarray, config: AssemblyConfig) -> list[dict[str, Any]]:
    binary = (mask > 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if count <= 1:
        return []
    areas = [int(v) for v in stats[1:, cv2.CC_STAT_AREA]]
    largest = max(areas)
    threshold = max(
        int(config.fragment_array_split_min_component_pixels),
        int(round(largest * float(config.fragment_array_split_min_component_area_ratio))),
    )
    specs: list[dict[str, Any]] = []
    for label in range(1, count):
        x, y, w, h, area = [int(v) for v in stats[label]]
        if area < threshold:
            continue
        specs.append({
            'label': label,
            'bbox': (x, y, w, h),
            'area': int(area),
        })
    specs.sort(key=lambda item: (-int(item['area']), int(item['bbox'][1]), int(item['bbox'][0])))
    return specs


def explode_fragments_by_connected_components(
    fragments: list[Fragment],
    config: AssemblyConfig,
) -> tuple[list[Fragment], list[dict[str, Any]]]:
    if not bool(config.fragment_array_split_multi_object_sources):
        return fragments, [
            {
                'sourceFragmentIndex': int(fragment.index),
                'sourceFile': fragment.name,
                'sourcePath': str(fragment.path),
                'sourceComponentCount': 1,
                'createdSubfragmentCount': 1,
                'splitApplied': False,
            }
            for fragment in fragments
        ]

    exploded: list[Fragment] = []
    split_reports: list[dict[str, Any]] = []
    next_index = 0
    for source_fragment in fragments:
        specs = _connected_component_specs(source_fragment.mask, config)
        if len(specs) <= 1:
            diagnostics = dict(source_fragment.diagnostics)
            diagnostics.update(
                {
                    'sourceImageIndex': int(source_fragment.index),
                    'sourceImageFile': source_fragment.name,
                    'sourceImagePath': str(source_fragment.path),
                    'sourceFragmentWasSplit': False,
                    'sourceFragmentCountWithinSource': 1,
                    'subfragmentIndexWithinSource': 0,
                    'derivedFragmentKey': f"{source_fragment.path}#cc00",
                }
            )
            exploded.append(
                Fragment(
                    index=next_index,
                    path=source_fragment.path,
                    name=source_fragment.name,
                    image=source_fragment.image,
                    gray=source_fragment.gray,
                    mask=source_fragment.mask,
                    crop_bbox_xywh=source_fragment.crop_bbox_xywh,
                    mask_area=source_fragment.mask_area,
                    principal_angle_deg=source_fragment.principal_angle_deg,
                    diagnostics=diagnostics,
                )
            )
            split_reports.append(
                {
                    'sourceFragmentIndex': int(source_fragment.index),
                    'sourceFile': source_fragment.name,
                    'sourcePath': str(source_fragment.path),
                    'sourceComponentCount': 1,
                    'createdSubfragmentCount': 1,
                    'splitApplied': False,
                }
            )
            next_index += 1
            continue

        created = 0
        sx, sy, _, _ = source_fragment.crop_bbox_xywh
        for sub_index, spec in enumerate(specs):
            label = int(spec['label'])
            local_mask = np.zeros_like(source_fragment.mask, dtype=np.uint8)
            count, labels, _, _ = cv2.connectedComponentsWithStats(
                (source_fragment.mask > 0).astype(np.uint8), connectivity=8
            )
            if count > 1:
                local_mask[labels == label] = 255
            else:
                local_mask = source_fragment.mask.copy()
            cropped_image, cropped_mask, bbox_local = crop_to_mask(
                source_fragment.image, local_mask, padding=4
            )
            bx, by, bw, bh = bbox_local
            absolute_bbox = (int(sx + bx), int(sy + by), int(bw), int(bh))
            diagnostics = dict(source_fragment.diagnostics)
            diagnostics.update(
                {
                    'sourceImageIndex': int(source_fragment.index),
                    'sourceImageFile': source_fragment.name,
                    'sourceImagePath': str(source_fragment.path),
                    'sourceFragmentWasSplit': True,
                    'sourceFragmentCountWithinSource': len(specs),
                    'subfragmentIndexWithinSource': int(sub_index),
                    'sourceMaskConnectedComponentLabel': label,
                    'sourceMaskConnectedComponentAreaPx': int(spec['area']),
                    'sourceMaskConnectedComponentBBoxXYWH': list(spec['bbox']),
                    'derivedFragmentKey': f"{source_fragment.path}#cc{sub_index:02d}",
                }
            )
            gray = to_gray_for_analysis(cropped_image)
            exploded.append(
                Fragment(
                    index=next_index,
                    path=source_fragment.path,
                    name=f"{Path(source_fragment.name).stem}#cc{sub_index:02d}{Path(source_fragment.name).suffix}",
                    image=cropped_image,
                    gray=gray,
                    mask=cropped_mask,
                    crop_bbox_xywh=absolute_bbox,
                    mask_area=int(np.count_nonzero(cropped_mask)),
                    principal_angle_deg=principal_angle_deg(cropped_mask),
                    diagnostics=diagnostics,
                )
            )
            created += 1
            next_index += 1
        split_reports.append(
            {
                'sourceFragmentIndex': int(source_fragment.index),
                'sourceFile': source_fragment.name,
                'sourcePath': str(source_fragment.path),
                'sourceComponentCount': int(len(specs)),
                'createdSubfragmentCount': int(created),
                'splitApplied': True,
            }
        )
    return exploded, split_reports
