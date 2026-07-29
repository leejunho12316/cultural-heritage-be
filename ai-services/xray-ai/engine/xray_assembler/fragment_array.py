from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

import cv2
import numpy as np

from .config import AssemblyConfig
from .image_ops import ensure_output_channels, normalize_preview, principal_angle_deg, rotate_bound
from .models import Fragment, SearchGeometry
from .routing import RouteDecision
from .stitching import (
    PairRegistration,
    _boundary_f1,
    _boundary_profile,
    _component_local_bundle,
    _crop_to_source_pair_transform,
    _direction_from_side_pair,
    _is_strong_registration,
    _maximum_spanning_forest,
    _phase_candidates,
    _normalize_angle,
    _pair_metrics,
    _pose_components,
    _profile_channel_ncc,
    _refine_poses_with_pose_graph,
    _registration_evidence_priority,
    _registration_score,
    _rotation_bound_matrix,
    _sift_candidate,
    _source_frame_gray_mask,
    _source_to_crop_pair_transform,
    _transform_points,
    _translation,
    matrices_to_placements,
    normalize_poses_to_canvas,
    render_mosaic,
)


def _external_contour(fragment: Fragment) -> np.ndarray | None:
    contours, _ = cv2.findContours(
        (fragment.mask > 0).astype(np.uint8),
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    if not contours:
        return None
    return max(contours, key=cv2.contourArea)


def _source_frame_consensus_registrations(
    fragments: list[Fragment], config: AssemblyConfig
) -> tuple[list[PairRegistration], list[dict[str, Any]]]:
    """Find duplicate fragment observations by shared source-frame translation.

    One similar contour is not sufficient: at least two disjoint fragments from
    the same two source frames must agree on one translation and independently
    pass pixel-overlap/NCC verification.  This is intentionally conservative.
    """
    if not bool(config.fragment_array_source_consensus_enabled):
        return [], []

    by_source: dict[int, list[Fragment]] = {}
    for fragment in fragments:
        if bool(fragment.diagnostics.get("residualNoiseGroup", False)):
            continue
        source_index = int(
            fragment.source_index
            if fragment.source_index is not None
            else fragment.diagnostics.get("originalSourceIndex", fragment.index)
        )
        by_source.setdefault(source_index, []).append(fragment)
    source_indices = sorted(by_source)
    if len(source_indices) < 2:
        return [], []

    contours = {fragment.index: _external_contour(fragment) for fragment in fragments}
    tolerance = float(config.fragment_array_source_consensus_translation_tolerance_px)
    maximum_shape_distance = float(
        config.fragment_array_source_consensus_max_shape_distance
    )
    maximum_area_ratio = float(config.fragment_array_source_consensus_max_area_ratio)
    minimum_overlap = float(config.fragment_array_source_consensus_min_overlap)
    minimum_intensity = float(
        config.fragment_array_source_consensus_min_intensity_ncc
    )
    minimum_gradient = float(config.fragment_array_source_consensus_min_gradient_ncc)
    minimum_matches = max(2, int(config.fragment_array_source_consensus_min_matches))
    window = max(1, int(config.fragment_array_source_consensus_window))

    accepted_edges: list[PairRegistration] = []
    diagnostics: list[dict[str, Any]] = []
    for source_pos, first_source in enumerate(source_indices):
        first_items = by_source[first_source]
        for second_source in source_indices[source_pos + 1 :]:
            if second_source - first_source > window:
                break
            second_items = by_source[second_source]
            tentative: list[dict[str, Any]] = []
            for first in first_items:
                first_contour = contours.get(first.index)
                if first_contour is None:
                    continue
                for second in second_items:
                    if bool(config.fragment_array_border_guided_registration) and not (
                        _is_partial_capture_candidate(first)
                        or _is_partial_capture_candidate(second)
                    ):
                        continue
                    second_contour = contours.get(second.index)
                    if second_contour is None:
                        continue
                    minimum_area = max(min(first.mask_area, second.mask_area), 1)
                    area_ratio = max(first.mask_area, second.mask_area) / minimum_area
                    if area_ratio > maximum_area_ratio:
                        continue
                    shape_distance = float(
                        cv2.matchShapes(
                            first_contour,
                            second_contour,
                            cv2.CONTOURS_MATCH_I1,
                            0.0,
                        )
                    )
                    if not np.isfinite(shape_distance) or shape_distance > maximum_shape_distance:
                        continue
                    first_x, first_y, first_w, first_h = first.crop_bbox_xywh
                    second_x, second_y, second_w, second_h = second.crop_bbox_xywh
                    translation = np.array(
                        [
                            (first_x + 0.5 * first_w) - (second_x + 0.5 * second_w),
                            (first_y + 0.5 * first_h) - (second_y + 0.5 * second_h),
                        ],
                        dtype=np.float64,
                    )
                    tentative.append(
                        {
                            "first": first,
                            "second": second,
                            "translation": translation,
                            "shapeDistance": shape_distance,
                            "areaRatio": float(area_ratio),
                            "cost": float(
                                shape_distance + 0.20 * abs(math.log(max(area_ratio, 1e-9)))
                            ),
                        }
                    )
            if len(tentative) < minimum_matches:
                continue

            best_cluster: list[dict[str, Any]] = []
            best_cost = float("inf")
            for seed in tentative:
                clustered = [
                    item
                    for item in tentative
                    if float(
                        np.linalg.norm(item["translation"] - seed["translation"])
                    )
                    <= tolerance
                ]
                clustered.sort(
                    key=lambda item: (
                        float(item["cost"]),
                        int(item["first"].index),
                        int(item["second"].index),
                    )
                )
                used_first: set[int] = set()
                used_second: set[int] = set()
                unique: list[dict[str, Any]] = []
                for item in clustered:
                    first_index = int(item["first"].index)
                    second_index = int(item["second"].index)
                    if first_index in used_first or second_index in used_second:
                        continue
                    used_first.add(first_index)
                    used_second.add(second_index)
                    unique.append(item)
                cluster_cost = sum(float(item["cost"]) for item in unique)
                if len(unique) > len(best_cluster) or (
                    len(unique) == len(best_cluster) and cluster_cost < best_cost
                ):
                    best_cluster = unique
                    best_cost = cluster_cost
            if len(best_cluster) < minimum_matches:
                continue

            consensus_translation = np.median(
                np.stack([item["translation"] for item in best_cluster], axis=0),
                axis=0,
            )
            verified: list[PairRegistration] = []
            candidate_debug: list[dict[str, Any]] = []
            for item in best_cluster:
                first = item["first"]
                second = item["second"]
                first_x, first_y, _, _ = first.crop_bbox_xywh
                second_x, second_y, _, _ = second.crop_bbox_xywh
                second_to_first = _translation(
                    float(second_x + consensus_translation[0] - first_x),
                    float(second_y + consensus_translation[1] - first_y),
                )
                overlap, intensity_ncc, gradient_ncc = _pair_metrics(
                    first.gray,
                    first.mask,
                    second.gray,
                    second.mask,
                    second_to_first,
                )
                passes = bool(
                    overlap >= minimum_overlap
                    and (
                        intensity_ncc >= minimum_intensity
                        or gradient_ncc >= minimum_gradient
                    )
                )
                candidate_debug.append(
                    {
                        "firstIndex": int(first.index),
                        "secondIndex": int(second.index),
                        "shapeDistance": float(item["shapeDistance"]),
                        "areaRatio": float(item["areaRatio"]),
                        "overlapRatio": float(overlap),
                        "intensityNcc": float(intensity_ncc),
                        "gradientNcc": float(gradient_ncc),
                        "accepted": passes,
                    }
                )
                if not passes:
                    continue
                support = len(best_cluster) / max(
                    len(first_items) + len(second_items), 1
                )
                score = _registration_score(
                    overlap, intensity_ncc, gradient_ncc, support
                )
                verified.append(
                    PairRegistration(
                        int(first.index),
                        int(second.index),
                        second_to_first,
                        float(score),
                        float(overlap),
                        float(intensity_ncc),
                        float(gradient_ncc),
                        "source_constellation_translation_consensus",
                        match_count=len(best_cluster),
                        inlier_count=len(best_cluster),
                        selection_confidence=float(np.clip(score, 0.0, 1.0)),
                        review_required=False,
                        selection_reason=(
                            "two_or_more_fragments_share_one_source_frame_translation"
                        ),
                    )
                )
            if len(verified) < minimum_matches:
                continue
            accepted_edges.extend(verified)
            diagnostics.append(
                {
                    "firstSourceIndex": int(first_source),
                    "secondSourceIndex": int(second_source),
                    "consensusTranslationXY": [
                        float(consensus_translation[0]),
                        float(consensus_translation[1]),
                    ],
                    "tentativeClusterCount": len(best_cluster),
                    "verifiedMatchCount": len(verified),
                    "matches": candidate_debug,
                }
            )
    return accepted_edges, diagnostics


def _fragment_source_index(fragment: Fragment) -> int:
    return int(
        fragment.source_index
        if fragment.source_index is not None
        else fragment.diagnostics.get("originalSourceIndex", fragment.index)
    )


def _rigid_angle_deg(matrix: np.ndarray) -> float:
    return float(
        _normalize_angle(math.degrees(math.atan2(matrix[1, 0], matrix[0, 0])))
    )


def _normalized_source_anchor(
    edge: PairRegistration,
    fragments_by_index: dict[int, Fragment],
) -> dict[str, Any] | None:
    first = fragments_by_index.get(int(edge.first_index))
    second = fragments_by_index.get(int(edge.second_index))
    if first is None or second is None:
        return None
    first_source = _fragment_source_index(first)
    second_source = _fragment_source_index(second)
    if first_source == second_source:
        return None
    source_transform = _crop_to_source_pair_transform(
        first, second, edge.second_to_first
    )
    if first_source < second_source:
        low_source, high_source = first_source, second_source
        high_to_low = source_transform
        low_fragment, high_fragment = first, second
    else:
        low_source, high_source = second_source, first_source
        high_to_low = np.linalg.inv(source_transform)
        low_fragment, high_fragment = second, first
    return {
        "lowSource": int(low_source),
        "highSource": int(high_source),
        "matrix": high_to_low,
        "angleDeg": _rigid_angle_deg(high_to_low),
        "translationXY": high_to_low[:2, 2].astype(np.float64),
        "edge": edge,
        "lowFragmentIndex": int(low_fragment.index),
        "highFragmentIndex": int(high_fragment.index),
    }


def _source_transform_anchor_cluster(
    anchors: list[dict[str, Any]],
    config: AssemblyConfig,
) -> list[dict[str, Any]]:
    if not anchors:
        return []
    rotation_tolerance = float(
        config.fragment_array_source_transform_rotation_tolerance_deg
    )
    translation_tolerance = float(
        config.fragment_array_source_transform_translation_tolerance_px
    )
    best_cluster: list[dict[str, Any]] = []
    best_key: tuple[int, float, float] = (-1, -1.0, -float("inf"))
    for seed in anchors:
        cluster: list[dict[str, Any]] = []
        residual_sum = 0.0
        for item in anchors:
            rotation_residual = abs(
                _normalize_angle(float(item["angleDeg"]) - float(seed["angleDeg"]))
            )
            translation_residual = float(
                np.linalg.norm(item["translationXY"] - seed["translationXY"])
            )
            if (
                rotation_residual <= rotation_tolerance
                and translation_residual <= translation_tolerance
            ):
                cluster.append(item)
                residual_sum += (
                    rotation_residual / max(rotation_tolerance, 1e-6)
                    + translation_residual / max(translation_tolerance, 1e-6)
                )
        confidence_sum = sum(
            float(item["edge"].selection_confidence) for item in cluster
        )
        key = (len(cluster), confidence_sum, -residual_sum)
        if key > best_key:
            best_key = key
            best_cluster = cluster
    return best_cluster


def _source_frame_transform_propagation_registrations(
    fragments: list[Fragment],
    anchor_edges: list[PairRegistration],
    config: AssemblyConfig,
) -> tuple[list[PairRegistration], list[dict[str, Any]]]:
    """Propagate a verified rigid source-frame transform to sibling objects.

    A direct SIFT edge or an already verified source-consensus edge can define
    the transform between two scanner frames.  Every other object pair from the
    same frames is evaluated under exactly that transform.  The frame pair is
    accepted only when at least two one-to-one object correspondences support
    it; a single similar fragment is therefore insufficient.
    """
    if not bool(config.fragment_array_source_transform_propagation_enabled):
        return [], []

    by_source: dict[int, list[Fragment]] = {}
    fragments_by_index = {int(fragment.index): fragment for fragment in fragments}
    for fragment in fragments:
        if bool(fragment.diagnostics.get("residualNoiseGroup", False)):
            continue
        by_source.setdefault(_fragment_source_index(fragment), []).append(fragment)

    anchors_by_source_pair: dict[tuple[int, int], list[dict[str, Any]]] = {}
    max_gap = max(1, int(config.fragment_array_source_transform_max_source_gap))
    for edge in anchor_edges:
        normalized = _normalized_source_anchor(edge, fragments_by_index)
        if normalized is None:
            continue
        source_pair = (int(normalized["lowSource"]), int(normalized["highSource"]))
        if source_pair[1] - source_pair[0] > max_gap:
            continue
        anchors_by_source_pair.setdefault(source_pair, []).append(normalized)

    minimum_verified = max(
        2, int(config.fragment_array_source_transform_min_verified_matches)
    )
    maximum_area_ratio = float(
        config.fragment_array_source_transform_max_area_ratio
    )
    minimum_overlap = float(config.fragment_array_source_transform_min_overlap)
    minimum_intensity = float(
        config.fragment_array_source_transform_min_intensity_ncc
    )
    minimum_gradient = float(
        config.fragment_array_source_transform_min_gradient_ncc
    )
    minimum_margin = max(
        0.0, float(config.fragment_array_source_transform_min_match_margin)
    )

    accepted_edges: list[PairRegistration] = []
    diagnostics: list[dict[str, Any]] = []
    for source_pair, anchors in sorted(anchors_by_source_pair.items()):
        low_source, high_source = source_pair
        low_items = by_source.get(low_source, [])
        high_items = by_source.get(high_source, [])
        cluster = _source_transform_anchor_cluster(anchors, config)
        if not cluster:
            continue
        representative = max(
            cluster,
            key=lambda item: (
                float(item["edge"].selection_confidence),
                int(item["edge"].inlier_count),
                float(item["edge"].score),
            ),
        )
        representative_edge: PairRegistration = representative["edge"]
        single_anchor = len(cluster) == 1
        inlier_ratio = representative_edge.inlier_count / max(
            representative_edge.match_count, 1
        )
        single_anchor_strong = bool(
            representative_edge.method.startswith("sift")
            and representative_edge.inlier_count
            >= int(config.fragment_array_source_transform_single_anchor_min_inliers)
            and inlier_ratio
            >= float(
                config.fragment_array_source_transform_single_anchor_min_inlier_ratio
            )
        )
        if single_anchor and not single_anchor_strong:
            diagnostics.append(
                {
                    "firstSourceIndex": int(low_source),
                    "secondSourceIndex": int(high_source),
                    "status": "rejected_weak_single_anchor",
                    "anchorCount": 1,
                    "anchorMethod": representative_edge.method,
                    "anchorInlierCount": int(representative_edge.inlier_count),
                    "anchorMatchCount": int(representative_edge.match_count),
                    "anchorInlierRatio": float(inlier_ratio),
                }
            )
            continue

        source_transform = representative["matrix"]
        anchor_pairs = {
            (int(item["lowFragmentIndex"]), int(item["highFragmentIndex"]))
            for item in cluster
        }
        candidates: list[dict[str, Any]] = []
        for low_fragment in low_items:
            for high_fragment in high_items:
                if bool(config.fragment_array_border_guided_registration) and not (
                    _is_partial_capture_candidate(low_fragment)
                    or _is_partial_capture_candidate(high_fragment)
                ):
                    continue
                minimum_area = max(
                    min(low_fragment.mask_area, high_fragment.mask_area), 1
                )
                area_ratio = (
                    max(low_fragment.mask_area, high_fragment.mask_area)
                    / minimum_area
                )
                if area_ratio > maximum_area_ratio:
                    continue
                high_crop_to_low_crop = _source_to_crop_pair_transform(
                    low_fragment, high_fragment, source_transform
                )
                overlap, intensity_ncc, gradient_ncc = _pair_metrics(
                    low_fragment.gray,
                    low_fragment.mask,
                    high_fragment.gray,
                    high_fragment.mask,
                    high_crop_to_low_crop,
                )
                is_anchor = (
                    int(low_fragment.index), int(high_fragment.index)
                ) in anchor_pairs
                passes = bool(
                    is_anchor
                    or (
                        overlap >= minimum_overlap
                        and (
                            intensity_ncc >= minimum_intensity
                            or gradient_ncc >= minimum_gradient
                        )
                    )
                )
                if not passes:
                    continue
                intensity_unit = float(
                    np.clip((intensity_ncc + 1.0) * 0.5, 0.0, 1.0)
                )
                gradient_unit = float(
                    np.clip((gradient_ncc + 1.0) * 0.5, 0.0, 1.0)
                )
                quality = float(
                    0.62 * np.clip(overlap, 0.0, 1.0)
                    + 0.23 * intensity_unit
                    + 0.15 * gradient_unit
                    + (0.08 if is_anchor else 0.0)
                )
                candidates.append(
                    {
                        "low": low_fragment,
                        "high": high_fragment,
                        "transform": high_crop_to_low_crop,
                        "overlap": float(overlap),
                        "intensityNcc": float(intensity_ncc),
                        "gradientNcc": float(gradient_ncc),
                        "quality": quality,
                        "isAnchor": is_anchor,
                    }
                )

        by_low: dict[int, list[dict[str, Any]]] = {}
        by_high: dict[int, list[dict[str, Any]]] = {}
        for item in candidates:
            by_low.setdefault(int(item["low"].index), []).append(item)
            by_high.setdefault(int(item["high"].index), []).append(item)
        for values in [*by_low.values(), *by_high.values()]:
            values.sort(key=lambda item: float(item["quality"]), reverse=True)

        selected: list[dict[str, Any]] = []
        used_low: set[int] = set()
        used_high: set[int] = set()
        for item in sorted(
            candidates,
            key=lambda candidate: (
                int(candidate["isAnchor"]),
                float(candidate["quality"]),
                float(candidate["overlap"]),
            ),
            reverse=True,
        ):
            low_index = int(item["low"].index)
            high_index = int(item["high"].index)
            if low_index in used_low or high_index in used_high:
                continue
            if not item["isAnchor"]:
                low_ranked = by_low[low_index]
                high_ranked = by_high[high_index]
                if low_ranked[0] is not item or high_ranked[0] is not item:
                    continue
                low_margin = (
                    float(item["quality"] - low_ranked[1]["quality"])
                    if len(low_ranked) > 1
                    else float("inf")
                )
                high_margin = (
                    float(item["quality"] - high_ranked[1]["quality"])
                    if len(high_ranked) > 1
                    else float("inf")
                )
                if min(low_margin, high_margin) < minimum_margin:
                    continue
            selected.append(item)
            used_low.add(low_index)
            used_high.add(high_index)

        selected_anchor_count = sum(bool(item["isAnchor"]) for item in selected)
        selected_propagated = [item for item in selected if not item["isAnchor"]]
        frame_pair_accepted = bool(
            len(selected) >= minimum_verified
            and (not single_anchor or len(selected_propagated) >= 1)
        )
        diagnostics.append(
            {
                "firstSourceIndex": int(low_source),
                "secondSourceIndex": int(high_source),
                "status": (
                    "accepted" if frame_pair_accepted else "rejected_insufficient_correspondences"
                ),
                "anchorCount": int(len(cluster)),
                "selectedAnchorCount": int(selected_anchor_count),
                "verifiedMatchCount": int(len(selected)),
                "propagatedMatchCount": int(len(selected_propagated)),
                "representativeTransform": source_transform.tolist(),
                "representativeRotationDeg": float(
                    _rigid_angle_deg(source_transform)
                ),
                "matches": [
                    {
                        "firstIndex": int(item["low"].index),
                        "secondIndex": int(item["high"].index),
                        "isAnchor": bool(item["isAnchor"]),
                        "overlapRatio": float(item["overlap"]),
                        "intensityNcc": float(item["intensityNcc"]),
                        "gradientNcc": float(item["gradientNcc"]),
                        "quality": float(item["quality"]),
                    }
                    for item in selected
                ],
            }
        )
        if not frame_pair_accepted:
            continue

        support = len(selected) / max(min(len(low_items), len(high_items)), 1)
        for item in selected_propagated:
            score = _registration_score(
                float(item["overlap"]),
                float(item["intensityNcc"]),
                float(item["gradientNcc"]),
                float(np.clip(support, 0.0, 1.0)),
            )
            accepted_edges.append(
                PairRegistration(
                    int(item["low"].index),
                    int(item["high"].index),
                    item["transform"],
                    float(score),
                    float(item["overlap"]),
                    float(item["intensityNcc"]),
                    float(item["gradientNcc"]),
                    "source_frame_transform_propagation",
                    match_count=int(len(candidates)),
                    inlier_count=int(len(selected)),
                    selection_confidence=float(
                        np.clip(item["quality"], 0.0, 1.0)
                    ),
                    review_required=False,
                    selection_reason=(
                        "direct_rigid_source_transform_verified_by_multiple_objects"
                    ),
                )
            )
    return accepted_edges, diagnostics


def _component_touch_sides(fragment: Fragment) -> dict[str, bool]:
    sides = fragment.diagnostics.get("component_touch_sides")
    if isinstance(sides, dict):
        return {name: bool(sides.get(name, False)) for name in ("top", "bottom", "left", "right")}
    legacy = fragment.diagnostics.get("touch_sides", {})
    return {name: bool(legacy.get(name, False)) for name in ("top", "bottom", "left", "right")}


def _best_boundary_profile_alignment_near(
    first_profile: np.ndarray,
    second_profile: np.ndarray,
    center_shift: int,
    radius: int,
    config: AssemblyConfig,
) -> tuple[float, float, float, float, int]:
    """Boundary NCC search restricted by component source coordinates."""
    first_length = len(first_profile)
    second_length = len(second_profile)
    maximum_shift = int(
        round(max(first_length, second_length) * float(config.frame_boundary_max_shift_ratio))
    )
    minimum_overlap = int(
        round(
            min(first_length, second_length)
            * float(config.frame_boundary_min_tangent_overlap_ratio)
        )
    )
    minimum_variance = float(config.frame_boundary_min_variance)
    low = max(-maximum_shift, int(center_shift) - max(0, int(radius)))
    high = min(maximum_shift, int(center_shift) + max(0, int(radius)))
    best: tuple[float, float, float, float, int] = (-1.0, 0.0, 0.0, 0.0, 0)
    for shift in range(low, high + 1):
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


def _source_boundary_profile_consensus_registrations(
    fragments: list[Fragment],
    anchor_edges: list[PairRegistration],
    config: AssemblyConfig,
) -> tuple[list[PairRegistration], list[dict[str, Any]]]:
    """Link texture-poor partial captures from original-frame edge evidence.

    Component contact is measured only against the original X-ray frame.  A
    boundary candidate is accepted when at least two one-to-one components from
    the same source-frame pair agree on one cardinal source translation, or when
    one candidate agrees with an independently verified direct rigid transform
    for the same source-frame pair.
    """
    if not bool(config.fragment_array_boundary_consensus_enabled):
        return [], []

    by_source: dict[int, list[Fragment]] = {}
    fragments_by_index = {int(fragment.index): fragment for fragment in fragments}
    for fragment in fragments:
        if bool(fragment.diagnostics.get("residualNoiseGroup", False)):
            continue
        if not _is_partial_capture_candidate(fragment):
            continue
        by_source.setdefault(_fragment_source_index(fragment), []).append(fragment)
    source_indices = sorted(by_source)
    if len(source_indices) < 2:
        return [], []

    anchor_by_pair: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for edge in anchor_edges:
        if not _is_strong_registration(edge, config):
            continue
        normalized = _normalized_source_anchor(edge, fragments_by_index)
        if normalized is None:
            continue
        pair = (int(normalized["lowSource"]), int(normalized["highSource"]))
        anchor_by_pair.setdefault(pair, []).append(normalized)

    profile_cache: dict[tuple[int, str], np.ndarray] = {}
    shape_cache: dict[int, tuple[int, int]] = {}
    for items in by_source.values():
        for fragment in items:
            source_gray, source_mask = _source_frame_gray_mask(fragment)
            shape_cache[int(fragment.index)] = source_mask.shape
            for side, touched in _component_touch_sides(fragment).items():
                if not touched:
                    continue
                profile_cache[(int(fragment.index), side)] = _boundary_profile(
                    source_gray,
                    source_mask,
                    side,
                    int(config.frame_boundary_strip_px),
                    max(0, int(config.fragment_frame_border_px)),
                )

    source_window = max(1, int(config.fragment_array_boundary_source_window))
    max_area_ratio = float(config.fragment_array_boundary_max_area_ratio)
    min_score = float(config.fragment_array_boundary_min_score)
    min_reliability = float(config.fragment_array_boundary_min_reliability)
    min_boundary = float(config.fragment_array_boundary_min_ncc)
    min_gradient = float(config.fragment_array_boundary_min_gradient_ncc)
    min_occupancy = float(config.fragment_array_boundary_min_occupancy_ncc)
    min_matches = max(2, int(config.fragment_array_boundary_min_matches))
    translation_tolerance = float(
        config.fragment_array_boundary_translation_tolerance_px
    )
    anchor_translation_tolerance = float(
        config.fragment_array_boundary_anchor_translation_tolerance_px
    )
    anchor_rotation_tolerance = float(
        config.fragment_array_boundary_anchor_rotation_tolerance_deg
    )
    desired_overlap = max(2.0, float(config.frame_continuation_overlap_px))

    accepted_edges: list[PairRegistration] = []
    diagnostics: list[dict[str, Any]] = []
    complementary = (
        ("right", "left"),
        ("left", "right"),
        ("bottom", "top"),
        ("top", "bottom"),
    )
    for source_position, low_source in enumerate(source_indices):
        for high_source in source_indices[source_position + 1 :]:
            if high_source - low_source > source_window:
                break
            low_items = by_source[low_source]
            high_items = by_source[high_source]
            candidates: list[dict[str, Any]] = []
            for low_fragment in low_items:
                low_sides = _component_touch_sides(low_fragment)
                low_h, low_w = shape_cache[int(low_fragment.index)]
                for high_fragment in high_items:
                    high_sides = _component_touch_sides(high_fragment)
                    high_h, high_w = shape_cache[int(high_fragment.index)]
                    minimum_area = max(
                        min(low_fragment.mask_area, high_fragment.mask_area), 1
                    )
                    area_ratio = (
                        max(low_fragment.mask_area, high_fragment.mask_area)
                        / minimum_area
                    )
                    if area_ratio > max_area_ratio:
                        continue
                    for low_side, high_side in complementary:
                        if not (
                            low_sides.get(low_side, False)
                            and high_sides.get(high_side, False)
                        ):
                            continue
                        low_profile = profile_cache.get(
                            (int(low_fragment.index), low_side)
                        )
                        high_profile = profile_cache.get(
                            (int(high_fragment.index), high_side)
                        )
                        if low_profile is None or high_profile is None:
                            continue
                        direction = _direction_from_side_pair(low_side, high_side)
                        low_x, low_y, low_width, low_height = low_fragment.crop_bbox_xywh
                        high_x, high_y, high_width, high_height = high_fragment.crop_bbox_xywh
                        if direction in {"left", "right"}:
                            predicted_shift = int(
                                round(
                                    (low_y + 0.5 * low_height)
                                    - (high_y + 0.5 * high_height)
                                )
                            )
                        else:
                            predicted_shift = int(
                                round(
                                    (low_x + 0.5 * low_width)
                                    - (high_x + 0.5 * high_width)
                                )
                            )
                        (
                            boundary_ncc,
                            boundary_gradient_ncc,
                            occupancy_ncc,
                            reliability,
                            tangent_shift,
                        ) = _best_boundary_profile_alignment_near(
                            low_profile,
                            high_profile,
                            predicted_shift,
                            int(config.fragment_array_boundary_tangent_search_radius_px),
                            config,
                        )
                        if direction == "right":
                            source_transform = _translation(
                                low_w - desired_overlap, float(tangent_shift)
                            )
                        elif direction == "left":
                            source_transform = _translation(
                                -(high_w - desired_overlap), float(tangent_shift)
                            )
                        elif direction == "down":
                            source_transform = _translation(
                                float(tangent_shift), low_h - desired_overlap
                            )
                        else:
                            source_transform = _translation(
                                float(tangent_shift), -(high_h - desired_overlap)
                            )
                        crop_transform = _source_to_crop_pair_transform(
                            low_fragment, high_fragment, source_transform
                        )
                        # Boundary consensus is generated from the original-frame
                        # edge strips only.  Full pair warping is intentionally
                        # deferred until a source-frame cluster is accepted; this
                        # keeps large 80+ frame arrays tractable and prevents a
                        # weak interior overlap from creating candidates.
                        boundary_unit = float(
                            np.clip((boundary_ncc + 1.0) * 0.5, 0.0, 1.0)
                        )
                        boundary_gradient_unit = float(
                            np.clip(
                                (boundary_gradient_ncc + 1.0) * 0.5,
                                0.0,
                                1.0,
                            )
                        )
                        occupancy_unit = float(
                            np.clip((occupancy_ncc + 1.0) * 0.5, 0.0, 1.0)
                        )
                        score = (
                            float(config.frame_boundary_base_score)
                            + 0.48 * boundary_unit
                            + 0.25 * boundary_gradient_unit
                            + 0.17 * occupancy_unit
                            + 0.10 * reliability
                        )
                        score *= 0.60 + 0.40 * float(reliability)
                        if not (
                            score >= min_score
                            and reliability >= min_reliability
                            and boundary_ncc >= min_boundary
                            and boundary_gradient_ncc >= min_gradient
                            and occupancy_ncc >= min_occupancy
                        ):
                            continue
                        candidates.append(
                            {
                                "low": low_fragment,
                                "high": high_fragment,
                                "sourceTransform": source_transform,
                                "cropTransform": crop_transform,
                                "direction": direction,
                                "score": float(score),
                                "boundaryNcc": float(boundary_ncc),
                                "boundaryGradientNcc": float(
                                    boundary_gradient_ncc
                                ),
                                "occupancyNcc": float(occupancy_ncc),
                                "reliability": float(reliability),
                                "tangentShift": int(tangent_shift),
                            }
                        )
            if not candidates:
                continue

            best_cluster: list[dict[str, Any]] = []
            best_key: tuple[int, float] = (-1, -float("inf"))
            for seed in candidates:
                close = [
                    item
                    for item in candidates
                    if item["direction"] == seed["direction"]
                    and float(
                        np.linalg.norm(
                            item["sourceTransform"][:2, 2]
                            - seed["sourceTransform"][:2, 2]
                        )
                    )
                    <= translation_tolerance
                ]
                close.sort(
                    key=lambda item: (
                        float(item["score"]),
                        float(item["boundaryNcc"]),
                        float(item["reliability"]),
                    ),
                    reverse=True,
                )
                used_low: set[int] = set()
                used_high: set[int] = set()
                unique: list[dict[str, Any]] = []
                for item in close:
                    low_index = int(item["low"].index)
                    high_index = int(item["high"].index)
                    if low_index in used_low or high_index in used_high:
                        continue
                    used_low.add(low_index)
                    used_high.add(high_index)
                    unique.append(item)
                key = (len(unique), sum(float(item["score"]) for item in unique))
                if key > best_key:
                    best_key = key
                    best_cluster = unique

            anchor_supported = False
            supporting_anchor: dict[str, Any] | None = None
            if best_cluster:
                representative_transform = best_cluster[0]["sourceTransform"]
                for anchor in anchor_by_pair.get((low_source, high_source), []):
                    anchor_matrix = anchor["matrix"]
                    rotation_residual = abs(_rigid_angle_deg(anchor_matrix))
                    translation_residual = float(
                        np.linalg.norm(
                            anchor_matrix[:2, 2]
                            - representative_transform[:2, 2]
                        )
                    )
                    if (
                        rotation_residual <= anchor_rotation_tolerance
                        and translation_residual <= anchor_translation_tolerance
                    ):
                        anchor_supported = True
                        supporting_anchor = anchor
                        break

            accepted = bool(
                len(best_cluster) >= min_matches
                or (len(best_cluster) >= 1 and anchor_supported)
            )
            if accepted:
                for item in best_cluster:
                    overlap, intensity_ncc, gradient_ncc = _pair_metrics(
                        item["low"].gray,
                        item["low"].mask,
                        item["high"].gray,
                        item["high"].mask,
                        item["cropTransform"],
                    )
                    item["overlap"] = float(overlap)
                    item["intensityNcc"] = float(intensity_ncc)
                    item["gradientNcc"] = float(gradient_ncc)
            diagnostics.append(
                {
                    "firstSourceIndex": int(low_source),
                    "secondSourceIndex": int(high_source),
                    "status": (
                        "accepted" if accepted else "rejected_insufficient_consensus"
                    ),
                    "rawCandidateCount": int(len(candidates)),
                    "consensusMatchCount": int(len(best_cluster)),
                    "anchorSupported": bool(anchor_supported),
                    "anchorMethod": (
                        None
                        if supporting_anchor is None
                        else str(supporting_anchor["edge"].method)
                    ),
                    "direction": (
                        None if not best_cluster else str(best_cluster[0]["direction"])
                    ),
                    "matches": [
                        {
                            "firstIndex": int(item["low"].index),
                            "secondIndex": int(item["high"].index),
                            "score": float(item["score"]),
                            "boundaryNcc": float(item["boundaryNcc"]),
                            "boundaryGradientNcc": float(
                                item["boundaryGradientNcc"]
                            ),
                            "boundaryOccupancyNcc": float(
                                item["occupancyNcc"]
                            ),
                            "boundaryReliability": float(item["reliability"]),
                            "tangentShift": int(item["tangentShift"]),
                            "direction": str(item["direction"]),
                        }
                        for item in best_cluster
                    ],
                }
            )
            if not accepted:
                continue
            support_count = max(
                len(best_cluster),
                min_matches if anchor_supported else len(best_cluster),
            )
            method = (
                "source_boundary_profile_anchor_supported"
                if anchor_supported and len(best_cluster) < min_matches
                else "source_boundary_profile_consensus"
            )
            for item in best_cluster:
                accepted_edges.append(
                    PairRegistration(
                        int(item["low"].index),
                        int(item["high"].index),
                        item["cropTransform"],
                        float(item["score"]),
                        float(item["overlap"]),
                        float(item["intensityNcc"]),
                        float(item["gradientNcc"]),
                        method,
                        match_count=int(len(candidates)),
                        inlier_count=int(support_count),
                        boundary_ncc=float(item["boundaryNcc"]),
                        boundary_gradient_ncc=float(
                            item["boundaryGradientNcc"]
                        ),
                        boundary_occupancy_ncc=float(item["occupancyNcc"]),
                        boundary_reliability=float(item["reliability"]),
                        direction=str(item["direction"]),
                        selection_confidence=float(
                            np.clip(item["score"], 0.0, 1.0)
                        ),
                        review_required=False,
                        selection_reason=(
                            "component_boundary_consensus_with_direct_source_anchor"
                            if method.endswith("anchor_supported")
                            else "two_or_more_components_share_boundary_source_translation"
                        ),
                    )
                )
    return accepted_edges, diagnostics


def _registration_pair_preference_key(
    edge: PairRegistration, config: AssemblyConfig
) -> tuple[int, float, float, float, float, float]:
    """Compare duplicate pair registrations without mixing score scales."""
    return (
        _registration_evidence_priority(edge, config),
        float(edge.selection_confidence),
        float(edge.score),
        float(edge.overlap_ratio),
        float(edge.intensity_ncc),
        float(edge.gradient_ncc),
    )


def _reference_component_count(mask: np.ndarray, minimum_area: int = 1) -> int:
    binary = (mask > 0).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if count <= 1:
        return 0
    return sum(
        int(stats[label, cv2.CC_STAT_AREA]) >= max(1, int(minimum_area))
        for label in range(1, count)
    )


def _segment_full_fragment_array_reference(
    reference_image: np.ndarray,
    fallback_mask: np.ndarray,
    config: AssemblyConfig,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Rebuild a fragment-array mask without dropping low-contrast rows.

    General reference segmentation uses Otsu because complete-object references
    can have complex backgrounds.  Fragment-array photographs are different:
    every detached object on the uniform board is a valid color slot.  A global
    Otsu threshold can retain the darker upper row while losing faint lower-row
    fragments.  This route-specific pass measures Lab distance from the border
    background, removes only obvious frame/scale artifacts, and retains all
    disconnected fragment components.
    """
    fallback = (fallback_mask > 0).astype(np.uint8) * 255
    fallback_count = _reference_component_count(fallback)
    if not bool(config.fragment_array_full_reference_segmentation_enabled):
        return fallback, {
            "enabled": False,
            "selected": "fallback_reference_mask",
            "fallbackComponentCount": int(fallback_count),
        }

    preview = normalize_preview(reference_image)
    original_h, original_w = preview.shape[:2]
    maximum_dimension = max(
        0, int(config.fragment_array_full_reference_analysis_max_dimension)
    )
    analysis_scale = 1.0
    analysis = preview
    if maximum_dimension > 0 and max(original_h, original_w) > maximum_dimension:
        analysis_scale = float(maximum_dimension) / float(max(original_h, original_w))
        analysis = cv2.resize(
            preview,
            (
                max(1, int(round(original_w * analysis_scale))),
                max(1, int(round(original_h * analysis_scale))),
            ),
            interpolation=cv2.INTER_AREA,
        )

    lab = cv2.cvtColor(analysis, cv2.COLOR_BGR2LAB).astype(np.float32)
    analysis_h, analysis_w = lab.shape[:2]
    border_width = max(3, int(round(min(analysis_h, analysis_w) * 0.02)))
    border = np.concatenate(
        [
            lab[:border_width, :, :].reshape(-1, 3),
            lab[-border_width:, :, :].reshape(-1, 3),
            lab[:, :border_width, :].reshape(-1, 3),
            lab[:, -border_width:, :].reshape(-1, 3),
        ],
        axis=0,
    )
    background_lab = np.median(border, axis=0)
    color_distance = np.linalg.norm(
        lab - background_lab[None, None, :], axis=2
    )
    distance_threshold = max(
        0.1,
        float(config.fragment_array_full_reference_border_distance_threshold),
    )
    raw = (color_distance >= distance_threshold).astype(np.uint8)

    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        raw, connectivity=8
    )
    raw_areas = [
        int(stats[label, cv2.CC_STAT_AREA]) for label in range(1, count)
    ]
    if not raw_areas:
        return fallback, {
            "enabled": True,
            "selected": "fallback_reference_mask",
            "reason": "no_border_distance_components",
            "fallbackComponentCount": int(fallback_count),
            "analysisScale": float(analysis_scale),
            "distanceThresholdLab": float(distance_threshold),
        }

    largest_area = max(raw_areas)
    minimum_area = max(
        int(config.fragment_array_full_reference_min_component_pixels),
        int(
            round(
                largest_area
                * float(
                    config.fragment_array_full_reference_min_component_area_ratio
                )
            )
        ),
    )
    maximum_border_thickness = max(
        2,
        int(
            round(
                min(analysis_h, analysis_w)
                * float(
                    config.fragment_array_full_reference_border_artifact_max_thickness_ratio
                )
            )
        ),
    )

    retained = np.zeros((analysis_h, analysis_w), dtype=np.uint8)
    removed_small = 0
    removed_border = 0
    removed_scale = 0
    retained_count = 0
    for label in range(1, count):
        x, y, width, height, area = [int(value) for value in stats[label]]
        if area < minimum_area:
            removed_small += 1
            continue

        touches_frame = bool(
            x <= 0
            or y <= 0
            or x + width >= analysis_w
            or y + height >= analysis_h
        )
        if touches_frame and min(width, height) <= maximum_border_thickness:
            removed_border += 1
            continue

        center_y_ratio = (y + 0.5 * height) / max(analysis_h, 1)
        width_ratio = width / max(analysis_w, 1)
        aspect_ratio = width / max(height, 1)
        # The calibration scale lies beneath the fragment array.  Requiring all
        # three conditions avoids deleting legitimate long horizontal shards.
        probable_scale_bar = bool(
            config.reference_remove_scale_bar
            and center_y_ratio >= 0.72
            and width_ratio >= 0.08
            and aspect_ratio >= 4.0
        )
        if probable_scale_bar:
            removed_scale += 1
            continue

        retained[labels == label] = 255
        retained_count += 1

    if analysis_scale != 1.0:
        candidate = cv2.resize(
            retained,
            (original_w, original_h),
            interpolation=cv2.INTER_NEAREST,
        )
    else:
        candidate = retained
    candidate = (candidate > 0).astype(np.uint8) * 255
    candidate_count = _reference_component_count(candidate)
    candidate_area_ratio = float(np.count_nonzero(candidate)) / max(
        candidate.size, 1
    )

    accepted = bool(
        candidate_count >= max(1, fallback_count)
        and candidate_count <= 500
        and 0.0001 <= candidate_area_ratio <= 0.80
    )
    selected = candidate if accepted else fallback
    return selected, {
        "enabled": True,
        "selected": (
            "full_fragment_array_border_distance_mask"
            if accepted
            else "fallback_reference_mask"
        ),
        "analysisScale": float(analysis_scale),
        "analysisShape": [int(analysis_h), int(analysis_w)],
        "distanceThresholdLab": float(distance_threshold),
        "minimumComponentAreaPxAtAnalysisScale": int(minimum_area),
        "fallbackComponentCount": int(fallback_count),
        "candidateComponentCount": int(candidate_count),
        "candidateAreaRatio": float(candidate_area_ratio),
        "removedSmallComponentCount": int(removed_small),
        "removedThinFrameArtifactCount": int(removed_border),
        "removedScaleBarComponentCount": int(removed_scale),
        "accepted": bool(accepted),
    }


def _attach_voronoi_search_regions(
    records: list[dict[str, Any]],
    canvas_shape: tuple[int, int],
    config: AssemblyConfig,
) -> list[dict[str, Any]]:
    """Attach a non-overlapping nearest-fragment search region to each slot.

    The exact white fragment mask remains the shape descriptor and scoring
    target.  The Voronoi region only supplies black context and a legal search
    boundary.  It is intentionally unrelated to X-ray frame-touching capture
    classification.
    """
    if not records:
        return records
    canvas_height, canvas_width = [int(value) for value in canvas_shape]
    retained = np.zeros((canvas_height, canvas_width), dtype=np.uint8)
    for record in records:
        x, y, width, height = [int(value) for value in record["bbox"]]
        retained[y : y + height, x : x + width][record["mask"] > 0] = 255

    # OpenCV labels every zero-connected component in the distance-transform
    # source.  Here retained fragment pixels are zero and the black background
    # is non-zero, so every background pixel receives the nearest fragment id.
    distance_source = np.where(retained > 0, 0, 255).astype(np.uint8)
    distance, nearest_labels = cv2.distanceTransformWithLabels(
        distance_source,
        cv2.DIST_L2,
        5,
        labelType=cv2.DIST_LABEL_CCOMP,
    )
    ratio = max(0.0, float(config.fragment_array_color_slot_voronoi_expansion_ratio))
    minimum_expansion = max(0, int(config.fragment_array_color_slot_voronoi_min_expansion_px))
    maximum_expansion = max(
        minimum_expansion,
        int(config.fragment_array_color_slot_voronoi_max_expansion_px),
    )
    epsilon_ratio = max(
        0.0,
        float(config.fragment_array_color_slot_voronoi_polygon_epsilon_ratio),
    )

    for record in records:
        x, y, width, height = [int(value) for value in record["bbox"]]
        local_labels = nearest_labels[y : y + height, x : x + width]
        label_values = local_labels[record["mask"] > 0]
        if label_values.size == 0:
            continue
        unique, counts = np.unique(label_values, return_counts=True)
        owner_label = int(unique[int(np.argmax(counts))])
        expansion = int(
            np.clip(
                round(max(width, height) * ratio),
                minimum_expansion,
                maximum_expansion,
            )
        )
        full_region = (
            (nearest_labels == owner_label)
            & (distance <= float(expansion) + 1e-6)
        )
        full_region[y : y + height, x : x + width][record["mask"] > 0] = True
        region_u8 = full_region.astype(np.uint8) * 255
        points = cv2.findNonZero(region_u8)
        if points is None:
            continue
        search_x, search_y, search_width, search_height = cv2.boundingRect(points)
        search_mask = region_u8[
            search_y : search_y + search_height,
            search_x : search_x + search_width,
        ].copy()
        contours, _ = cv2.findContours(
            search_mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        polygon_global: list[list[int]] = []
        if contours:
            contour = max(contours, key=cv2.contourArea)
            epsilon = max(1.0, epsilon_ratio * cv2.arcLength(contour, True))
            polygon = cv2.approxPolyDP(contour, epsilon, True)
            polygon_global = [
                [int(point[0][0] + search_x), int(point[0][1] + search_y)]
                for point in polygon
            ]

        # This diagnostic should normally be false.  It may be true only when a
        # color fragment itself reaches the original reference-frame boundary.
        region_boundary = cv2.subtract(
            search_mask,
            cv2.erode(search_mask, np.ones((3, 3), dtype=np.uint8)),
        )
        slot_in_search = np.zeros_like(search_mask)
        local_x = x - search_x
        local_y = y - search_y
        slot_in_search[
            local_y : local_y + height,
            local_x : local_x + width,
        ][record["mask"] > 0] = 255
        fragment_touches_search_boundary = bool(
            np.any((region_boundary > 0) & (slot_in_search > 0))
        )
        record.update(
            {
                "searchMask": search_mask,
                "searchBBox": (
                    int(search_x),
                    int(search_y),
                    int(search_width),
                    int(search_height),
                ),
                "searchPolygonXY": polygon_global,
                "searchExpansionPx": int(expansion),
                "fragmentTouchesSearchBoundary": fragment_touches_search_boundary,
                "searchRegionMethod": "nearest_fragment_voronoi_clipped_by_expansion",
            }
        )
    return records


def _compact_reference_slot_records(
    mask: np.ndarray,
    minimum_area_ratio: float,
    config: AssemblyConfig | None = None,
) -> list[dict[str, Any]]:
    """Return per-slot cropped masks without allocating one full canvas per slot."""
    binary = (mask > 0).astype(np.uint8) * 255
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    raw: list[dict[str, Any]] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        if width <= 0 or height <= 0:
            continue
        local_contour = contour.copy()
        local_contour[:, 0, 0] -= x
        local_contour[:, 0, 1] -= y
        contour_mask = np.zeros((height, width), dtype=np.uint8)
        cv2.drawContours(contour_mask, [local_contour], -1, 255, thickness=-1)
        cropped = cv2.bitwise_and(binary[y : y + height, x : x + width], contour_mask)
        area = int(np.count_nonzero(cropped))
        if area > 0:
            raw.append({"mask": cropped, "bbox": (x, y, width, height), "area": area})
    if not raw:
        return []
    largest = max(int(record["area"]) for record in raw)
    threshold = max(12, int(round(largest * minimum_area_ratio)))
    records = [record for record in raw if int(record["area"]) >= threshold]
    records.sort(key=lambda item: int(item["area"]), reverse=True)
    if config is not None and bool(
        config.fragment_array_color_slot_voronoi_search_enabled
    ):
        records = _attach_voronoi_search_regions(records, binary.shape, config)
    return records


def _render_reference_slot_search_overlay(
    preview: np.ndarray,
    records: list[dict[str, Any]],
) -> np.ndarray:
    canvas = normalize_preview(preview).copy()
    for slot_index, record in enumerate(records):
        polygon_points = record.get("searchPolygonXY", [])
        if polygon_points:
            polygon = np.asarray(polygon_points, dtype=np.int32).reshape(-1, 1, 2)
            cv2.polylines(
                canvas,
                [polygon],
                isClosed=True,
                color=(0, 0, 255),
                thickness=2,
                lineType=cv2.LINE_AA,
            )
        x, y, width, height = [int(value) for value in record["bbox"]]
        local_contours, _ = cv2.findContours(
            (record["mask"] > 0).astype(np.uint8),
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        for contour in local_contours:
            contour = contour.copy()
            contour[:, 0, 0] += x
            contour[:, 0, 1] += y
            cv2.polylines(
                canvas,
                [contour],
                isClosed=True,
                color=(0, 255, 0),
                thickness=1,
                lineType=cv2.LINE_AA,
            )
        cv2.putText(
            canvas,
            f"{slot_index:02d}",
            (x, max(18, y - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (0, 255, 255),
            1,
            cv2.LINE_AA,
        )
    return canvas


def _fragment_component_areas(
    fragments: list[Fragment], minimum_area_ratio: float
) -> list[int]:
    areas: list[int] = []
    for fragment in fragments:
        count, _, stats, _ = cv2.connectedComponentsWithStats(
            (fragment.mask > 0).astype(np.uint8), connectivity=8
        )
        if count <= 1:
            continue
        raw = [int(value) for value in stats[1:, cv2.CC_STAT_AREA]]
        largest = max(raw)
        threshold = max(8, int(round(largest * minimum_area_ratio)))
        areas.extend(area for area in raw if area >= threshold)
    return areas


def _estimate_reference_scale(
    reference_mask: np.ndarray,
    fragments: list[Fragment],
    total_xray_area: int,
    config: AssemblyConfig,
) -> tuple[float, dict[str, Any]]:
    reference_area = int(np.count_nonzero(reference_mask))
    total_area_scale = math.sqrt(
        total_xray_area
        / max(reference_area * float(config.reference_fill_ratio), 1.0)
    )
    reference_records = _compact_reference_slot_records(
        reference_mask,
        minimum_area_ratio=float(config.fragment_array_reference_min_component_area_ratio),
        config=config,
    )
    reference_areas = [int(record["area"]) for record in reference_records]
    xray_areas = _fragment_component_areas(
        fragments,
        minimum_area_ratio=float(config.fragment_array_reference_min_component_area_ratio),
    )
    component_scale = None
    if reference_areas and xray_areas:
        component_scale = math.sqrt(
            float(np.median(xray_areas)) / max(float(np.median(reference_areas)), 1.0)
        )
    selected = total_area_scale
    if component_scale is not None:
        selected = min(
            selected,
            component_scale * float(config.fragment_array_component_scale_multiplier),
        )
    maximum_reference_dimension = int(config.fragment_array_reference_max_dimension)
    dimension_cap_scale = (
        float(maximum_reference_dimension) / max(reference_mask.shape)
        if maximum_reference_dimension > 0
        else float("inf")
    )
    selected = max(1e-3, min(selected, dimension_cap_scale))
    return float(selected), {
        "method": "total_area_capped_by_component_median_and_dimension",
        "totalAreaScale": float(total_area_scale),
        "componentMedianScale": (
            float(component_scale) if component_scale is not None else None
        ),
        "componentScaleMultiplier": float(config.fragment_array_component_scale_multiplier),
        "dimensionCapScale": float(dimension_cap_scale),
        "selectedScale": float(selected),
        "referenceSlotSampleCount": len(reference_areas),
        "xrayComponentSampleCount": len(xray_areas),
    }


def _render_mosaic_first_original_pixel(
    fragments: list[Fragment],
    poses: list[np.ndarray],
    canvas_size: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Memory-bounded renderer for reference-anchored source-frame placement.

    It never blends source pixels.  The first original pixel occupying an output
    location is retained while a uint8 overlap-count canvas records every
    additional source observation.  This avoids the full-canvas float32
    distance-weight buffer used by the general renderer.
    """
    canvas_width, canvas_height = canvas_size
    output_channels = (
        3
        if any(fragment.image.ndim == 3 and fragment.image.shape[2] >= 3 for fragment in fragments)
        else 1
    )
    output_dtype = np.result_type(*[fragment.image.dtype for fragment in fragments])
    output_shape = (
        (canvas_height, canvas_width)
        if output_channels == 1
        else (canvas_height, canvas_width, 3)
    )
    mosaic = np.zeros(output_shape, dtype=output_dtype)
    mask_canvas = np.zeros((canvas_height, canvas_width), dtype=np.uint8)
    counts = np.zeros((canvas_height, canvas_width), dtype=np.uint8)

    for fragment, pose in zip(fragments, poses):
        height, width = fragment.mask.shape
        corners = _transform_points(
            pose,
            np.array(
                [[0.0, 0.0], [width, 0.0], [width, height], [0.0, height]],
                dtype=np.float64,
            ),
        )
        x0 = max(0, int(math.floor(float(corners[:, 0].min()))))
        y0 = max(0, int(math.floor(float(corners[:, 1].min()))))
        x1 = min(canvas_width, int(math.ceil(float(corners[:, 0].max()))))
        y1 = min(canvas_height, int(math.ceil(float(corners[:, 1].max()))))
        if x1 <= x0 or y1 <= y0:
            continue
        local_size = (x1 - x0, y1 - y0)
        local_transform = _translation(-x0, -y0) @ pose
        warped_mask = cv2.warpAffine(
            (fragment.mask > 0).astype(np.uint8) * 255,
            local_transform[:2].astype(np.float32),
            local_size,
            flags=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        ) > 0
        if not np.any(warped_mask):
            continue
        image = ensure_output_channels(fragment.image, output_channels, output_dtype)
        warped_image = cv2.warpAffine(
            image,
            local_transform[:2].astype(np.float32),
            local_size,
            flags=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        roi_counts = counts[y0:y1, x0:x1]
        roi_mask = mask_canvas[y0:y1, x0:x1]
        incrementable = warped_mask & (roi_counts < np.iinfo(np.uint8).max)
        roi_counts[incrementable] += 1
        write = warped_mask & (roi_mask == 0)
        if output_channels == 1:
            mosaic[y0:y1, x0:x1][write] = warped_image[write]
        else:
            mosaic[y0:y1, x0:x1][write, :] = warped_image[write, :]
        roi_mask[warped_mask] = 255
    return mosaic, mask_canvas, counts


def _render_mosaic_local(
    fragments: list[Fragment],
    poses: list[np.ndarray],
    canvas_size: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Render only transformed bounding boxes, avoiding a full-canvas warp per source."""
    canvas_width, canvas_height = canvas_size
    output_channels = (
        3
        if any(fragment.image.ndim == 3 and fragment.image.shape[2] >= 3 for fragment in fragments)
        else 1
    )
    output_dtype = np.result_type(*[fragment.image.dtype for fragment in fragments])
    output_shape = (
        (canvas_height, canvas_width)
        if output_channels == 1
        else (canvas_height, canvas_width, 3)
    )
    mosaic = np.zeros(output_shape, dtype=output_dtype)
    mask_canvas = np.zeros((canvas_height, canvas_width), dtype=np.uint8)
    best_weight = np.full((canvas_height, canvas_width), -1.0, dtype=np.float32)
    counts = np.zeros((canvas_height, canvas_width), dtype=np.uint16)

    for fragment, pose in zip(fragments, poses):
        height, width = fragment.mask.shape
        corners = _transform_points(
            pose,
            np.array(
                [[0.0, 0.0], [width, 0.0], [width, height], [0.0, height]],
                dtype=np.float64,
            ),
        )
        x0 = max(0, int(math.floor(float(corners[:, 0].min()))))
        y0 = max(0, int(math.floor(float(corners[:, 1].min()))))
        x1 = min(canvas_width, int(math.ceil(float(corners[:, 0].max()))))
        y1 = min(canvas_height, int(math.ceil(float(corners[:, 1].max()))))
        if x1 <= x0 or y1 <= y0:
            continue
        local_size = (x1 - x0, y1 - y0)
        local_transform = _translation(-x0, -y0) @ pose
        source_mask = (fragment.mask > 0).astype(np.uint8) * 255
        warped_mask = cv2.warpAffine(
            source_mask,
            local_transform[:2].astype(np.float32),
            local_size,
            flags=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        ) > 0
        if not np.any(warped_mask):
            continue
        image = ensure_output_channels(fragment.image, output_channels, output_dtype)
        warped_image = cv2.warpAffine(
            image,
            local_transform[:2].astype(np.float32),
            local_size,
            flags=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        distance = cv2.distanceTransform(
            (source_mask > 0).astype(np.uint8), cv2.DIST_L2, 3
        )
        warped_weight = cv2.warpAffine(
            distance.astype(np.float32),
            local_transform[:2].astype(np.float32),
            local_size,
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        roi_counts = counts[y0:y1, x0:x1]
        roi_best = best_weight[y0:y1, x0:x1]
        roi_mask = mask_canvas[y0:y1, x0:x1]
        roi_counts[warped_mask] += 1
        roi_mask[warped_mask] = 255
        write = warped_mask & (warped_weight > roi_best)
        if output_channels == 1:
            mosaic[y0:y1, x0:x1][write] = warped_image[write]
        else:
            mosaic[y0:y1, x0:x1][write, :] = warped_image[write, :]
        roi_best[write] = warped_weight[write]
    return mosaic, mask_canvas, counts


def _strict_array_registration(edge: PairRegistration, config: AssemblyConfig) -> bool:
    """Accept only image-supported overlap; filename/sequence continuation is excluded."""
    if not _is_strong_registration(edge, config):
        return False
    if edge.method.startswith("sift"):
        inlier_ratio = edge.inlier_count / max(edge.match_count, 1)
        return (
            edge.inlier_count >= max(
                int(config.sift_min_inliers),
                int(config.fragment_array_min_sift_inliers),
            )
            and inlier_ratio >= float(config.fragment_array_min_sift_inlier_ratio)
            and edge.overlap_ratio >= float(config.fragment_array_min_registration_overlap)
            and edge.intensity_ncc >= float(config.fragment_array_min_intensity_ncc)
        )
    if edge.method.startswith("phase"):
        return (
            edge.score >= float(config.fragment_array_min_phase_score)
            and edge.overlap_ratio >= float(config.fragment_array_min_registration_overlap)
            and edge.intensity_ncc >= float(config.fragment_array_min_intensity_ncc)
            and edge.gradient_ncc >= float(config.fragment_array_min_gradient_ncc)
            and edge.phase_response >= float(config.fragment_array_min_phase_response)
        )
    return False


def _fragment_array_descriptor(fragment: Fragment) -> tuple[float, float, float]:
    height, width = fragment.mask.shape
    area = max(float(fragment.mask_area), 1.0)
    aspect = max(width, 1) / max(height, 1)
    angle = float(fragment.principal_angle_deg)
    return float(math.log(area)), float(math.log(max(aspect, 1e-6))), angle


def _same_source_image(first: Fragment, second: Fragment) -> bool:
    first_source = getattr(first, 'source_path', None) or first.diagnostics.get('sourceImagePath') or first.path
    second_source = getattr(second, 'source_path', None) or second.diagnostics.get('sourceImagePath') or second.path
    first_index = getattr(first, 'source_index', None)
    second_index = getattr(second, 'source_index', None)
    if first_index is not None and second_index is not None and int(first_index) == int(second_index):
        return True
    return str(first_source) == str(second_source)


def _fragment_capture_role(fragment: Fragment) -> str:
    if bool(fragment.diagnostics.get("residualNoiseGroup", False)):
        return "residual_noise_group"
    explicit = str(fragment.diagnostics.get("capture_role", "")).strip()
    if explicit:
        return explicit
    touches = bool(
        fragment.diagnostics.get(
            "component_touches_frame",
            fragment.diagnostics.get("touches_frame", False),
        )
    )
    return (
        "partial_capture_candidate"
        if touches
        else "independent_fragment_candidate"
    )


def _is_partial_capture_candidate(fragment: Fragment) -> bool:
    return _fragment_capture_role(fragment) == "partial_capture_candidate"


def _fragment_array_candidate_pairs(
    fragments: list[Fragment], config: AssemblyConfig
) -> list[tuple[int, int]]:
    count = len(fragments)
    if count <= 1:
        return []
    descriptors = [_fragment_array_descriptor(fragment) for fragment in fragments]
    neighbors: list[list[tuple[float, int]]] = [[] for _ in fragments]
    area_limit = float(config.fragment_array_pair_area_ratio_limit)
    aspect_limit = float(config.fragment_array_pair_aspect_ratio_limit)
    large_mode = count >= int(config.fragment_array_large_pair_threshold)
    source_window = max(1, int(config.fragment_array_large_source_index_window))
    for first_index in range(count):
        first = fragments[first_index]
        if bool(first.diagnostics.get("residualNoiseGroup", False)):
            continue
        log_area_first, log_aspect_first, angle_first = descriptors[first_index]
        for second_index in range(first_index + 1, count):
            second = fragments[second_index]
            if bool(second.diagnostics.get("residualNoiseGroup", False)):
                continue
            if _same_source_image(first, second):
                continue
            if bool(config.fragment_array_border_guided_registration) and not (
                _is_partial_capture_candidate(first)
                or _is_partial_capture_candidate(second)
            ):
                # Two fully enclosed objects are treated as independent physical
                # fragments.  They are matched to separate color-reference slots
                # later instead of being tested as an X-ray overlap pair.
                continue
            if large_mode:
                first_source = int(
                    first.source_index if first.source_index is not None else first.index
                )
                second_source = int(
                    second.source_index if second.source_index is not None else second.index
                )
                if abs(first_source - second_source) > source_window:
                    continue
            log_area_second, log_aspect_second, angle_second = descriptors[second_index]
            area_ratio = math.exp(abs(log_area_first - log_area_second))
            aspect_ratio = math.exp(abs(log_aspect_first - log_aspect_second))
            if area_ratio > area_limit or aspect_ratio > aspect_limit:
                continue
            angle_delta = abs(_normalize_angle(angle_first - angle_second))
            angle_delta = min(angle_delta, 180.0 - angle_delta)
            distance = abs(log_area_first - log_area_second) + 0.7 * abs(
                log_aspect_first - log_aspect_second
            ) + 0.15 * (angle_delta / 90.0)
            neighbors[first_index].append((distance, second_index))
            neighbors[second_index].append((distance, first_index))
    pair_limit = max(
        1,
        int(
            config.fragment_array_large_pair_neighbors
            if large_mode
            else config.fragment_array_pair_neighbors
        ),
    )
    pairs: set[tuple[int, int]] = set()
    for first_index, candidates in enumerate(neighbors):
        candidates.sort(key=lambda item: (item[0], item[1]))
        for _, second_index in candidates[:pair_limit]:
            pairs.add((min(first_index, second_index), max(first_index, second_index)))
    if not pairs:
        for first_index in range(count):
            for second_index in range(first_index + 1, count):
                first = fragments[first_index]
                second = fragments[second_index]
                if _same_source_image(first, second):
                    continue
                if bool(config.fragment_array_border_guided_registration) and not (
                    _is_partial_capture_candidate(first)
                    or _is_partial_capture_candidate(second)
                ):
                    continue
                pairs.add((first_index, second_index))
    return sorted(pairs)


def _registration_components(
    fragments: list[Fragment],
    config: AssemblyConfig,
    route: RouteDecision,
) -> tuple[
    list[PairRegistration],
    list[PairRegistration],
    list[np.ndarray],
    list[list[int]],
    dict[str, Any],
]:
    capture_roles = [_fragment_capture_role(fragment) for fragment in fragments]
    partial_indices = [
        fragment.index
        for fragment, role in zip(fragments, capture_roles)
        if role == "partial_capture_candidate"
    ]
    independent_indices = [
        fragment.index
        for fragment, role in zip(fragments, capture_roles)
        if role == "independent_fragment_candidate"
    ]
    residual_indices = [
        fragment.index
        for fragment, role in zip(fragments, capture_roles)
        if role == "residual_noise_group"
    ]
    no_registration_candidates = bool(
        bool(config.fragment_array_border_guided_registration)
        and not partial_indices
    )
    legacy_independent_shortcut = bool(
        not bool(config.fragment_array_border_guided_registration)
        and route.resolved_capture_mode == "independent_fragments"
        and not any(
            bool(fragment.diagnostics.get("splitFromMultiObjectSource", False))
            for fragment in fragments
        )
    )
    if no_registration_candidates or legacy_independent_shortcut:
        raw_poses = [np.eye(3, dtype=np.float64) for _ in fragments]
        components = [[fragment.index] for fragment in fragments]
        return [], [], raw_poses, components, {
            "registrationMode": "fragment_array_border_classified_independent_sources",
            "candidateAlternatives": {},
            "sequenceResolution": [],
            "sequenceBreaks": [],
            "extendedSiftAnchors": [],
            "cycleConsistency": [],
            "poseGraph": [],
            "captureRolePolicy": (
                "register_only_pairs_with_at_least_one_frame_touching_component"
                if bool(config.fragment_array_border_guided_registration)
                else "legacy_capture_mode"
            ),
            "partialCaptureCandidateIndices": partial_indices,
            "independentFragmentCandidateIndices": independent_indices,
            "residualNoiseGroupIndices": residual_indices,
            "forestSelection": {
                "mode": "strong_image_evidence_only",
                "plausibleCandidateCount": 0,
                "strongCandidateCount": 0,
                "weakCandidateCount": 0,
                "strongSelectedCount": 0,
                "weakSelectedCount": 0,
                "weakSelectedDominates": False,
            },
            "rejectedNonImageSupportedCount": 0,
        }

    # The complete-reference registration routine intentionally evaluates weak
    # sequence continuation and retains many phase alternatives. Fragment-array
    # mode must not use those priors, and retaining those alternatives is also
    # unnecessarily memory-heavy for cases with dozens of source frames.
    # Evaluate candidate pairs directly and keep at most one accepted, strongly
    # image-supported edge per pair.
    attempted_pairs = _fragment_array_candidate_pairs(fragments, config)
    allow_phase = len(fragments) <= int(config.fragment_array_phase_max_fragments)
    strict: list[PairRegistration] = []
    sift_accepted = 0
    phase_accepted = 0
    rejected_pairs = 0
    candidate_pair_diagnostics: list[dict[str, Any]] = []
    for first_index, second_index in attempted_pairs:
        first = fragments[first_index]
        second = fragments[second_index]
        accepted: PairRegistration | None = None
        pair_debug: dict[str, Any] = {
            "firstIndex": int(first_index),
            "secondIndex": int(second_index),
            "firstRole": capture_roles[first_index],
            "secondRole": capture_roles[second_index],
            "firstSourceIndex": (
                None if first.source_index is None else int(first.source_index)
            ),
            "secondSourceIndex": (
                None if second.source_index is None else int(second.source_index)
            ),
            "phaseEvaluated": bool(allow_phase),
        }
        sift = _sift_candidate(first, second, config)
        if sift is None:
            pair_debug["sift"] = {"candidateFound": False, "accepted": False}
        else:
            sift_passes = bool(_strict_array_registration(sift, config))
            pair_debug["sift"] = {
                "candidateFound": True,
                "accepted": sift_passes,
                "score": float(sift.score),
                "overlapRatio": float(sift.overlap_ratio),
                "intensityNcc": float(sift.intensity_ncc),
                "gradientNcc": float(sift.gradient_ncc),
                "matchCount": int(sift.match_count),
                "inlierCount": int(sift.inlier_count),
            }
            if sift_passes:
                accepted = sift
                sift_accepted += 1
        if accepted is None and allow_phase:
            phase_candidates = _phase_candidates(first, second, config)
            strict_phase = [
                edge
                for edge in phase_candidates
                if _strict_array_registration(edge, config)
            ]
            pair_debug["phase"] = {
                "candidateCount": int(len(phase_candidates)),
                "strictCandidateCount": int(len(strict_phase)),
            }
            if strict_phase:
                accepted = max(
                    strict_phase,
                    key=lambda edge: (
                        edge.score,
                        edge.overlap_ratio,
                        edge.intensity_ncc,
                        edge.gradient_ncc,
                    ),
                )
                phase_accepted += 1
        elif accepted is None:
            pair_debug["phase"] = {
                "candidateCount": 0,
                "strictCandidateCount": 0,
                "skipped": True,
            }
        if accepted is None:
            rejected_pairs += 1
            pair_debug["status"] = "rejected_no_strong_image_evidence"
            candidate_pair_diagnostics.append(pair_debug)
            continue
        pair_debug["status"] = "accepted"
        pair_debug["selectedMethod"] = str(accepted.method)
        pair_debug["selectedScore"] = float(accepted.score)
        pair_debug["selectedOverlapRatio"] = float(accepted.overlap_ratio)
        pair_debug["selectedIntensityNcc"] = float(accepted.intensity_ncc)
        pair_debug["selectedGradientNcc"] = float(accepted.gradient_ncc)
        candidate_pair_diagnostics.append(pair_debug)
        strict.append(
            replace(
                accepted,
                review_required=False,
                selection_confidence=float(np.clip(accepted.score, 0.0, 1.0)),
                selection_reason="fragment_array_strong_image_supported_overlap",
            )
        )

    consensus_edges, source_consensus_debug = _source_frame_consensus_registrations(
        fragments, config
    )
    propagated_edges, source_transform_debug = (
        _source_frame_transform_propagation_registrations(
            fragments, [*strict, *consensus_edges], config
        )
    )
    boundary_edges, source_boundary_debug = (
        _source_boundary_profile_consensus_registrations(
            fragments, [*strict, *consensus_edges, *propagated_edges], config
        )
    )
    edge_by_pair: dict[tuple[int, int], PairRegistration] = {}
    for edge in [
        *strict,
        *consensus_edges,
        *propagated_edges,
        *boundary_edges,
    ]:
        key = (min(edge.first_index, edge.second_index), max(edge.first_index, edge.second_index))
        previous = edge_by_pair.get(key)
        if previous is None or _registration_pair_preference_key(
            edge, config
        ) > _registration_pair_preference_key(previous, config):
            edge_by_pair[key] = edge
    strict = sorted(
        edge_by_pair.values(), key=lambda edge: (edge.first_index, edge.second_index)
    )

    tree_edges, forest_debug = _maximum_spanning_forest(
        len(fragments), strict, config, fragments
    )
    raw_poses, components = _pose_components(len(fragments), tree_edges, fragments)
    raw_poses, pose_graph_debug = _refine_poses_with_pose_graph(
        raw_poses,
        components,
        strict,
        fragments,
        config,
    )
    component_source_counts = [
        len(
            {
                str(fragments[index].source_path or fragments[index].path)
                for index in component
            }
        )
        for component in components
    ]
    partial_component_count = sum(
        any(index in set(partial_indices) for index in component)
        for component in components
    )
    unconsolidated_partial_component_count = sum(
        len(component) == 1 and component[0] in set(partial_indices)
        for component in components
    )
    registration_debug = {
        "registrationMode": "fragment_array_border_guided_strong_image_evidence_only",
        "captureRolePolicy": (
            "register_only_pairs_with_at_least_one_frame_touching_component"
            if bool(config.fragment_array_border_guided_registration)
            else "all_cross_source_pairs_subject_to_shape_prefilter"
        ),
        "partialCaptureCandidateIndices": partial_indices,
        "independentFragmentCandidateIndices": independent_indices,
        "residualNoiseGroupIndices": residual_indices,
        "candidateAlternatives": {},
        "sequenceResolution": [],
        "sequenceBreaks": [],
        "extendedSiftAnchors": [],
        "cycleConsistency": [],
        "forestSelection": forest_debug,
        "poseGraph": pose_graph_debug,
        "attemptedPairCount": len(attempted_pairs),
        "candidatePairDiagnostics": candidate_pair_diagnostics,
        "strictRegistrationCount": len(strict),
        "resultComponentCount": int(len(components)),
        "multiSourceComponentCount": int(
            sum(source_count > 1 for source_count in component_source_counts)
        ),
        "partialCaptureComponentCount": int(partial_component_count),
        "unconsolidatedPartialCaptureComponentCount": int(
            unconsolidated_partial_component_count
        ),
        "componentSourceImageCounts": [
            int(source_count) for source_count in component_source_counts
        ],
        "siftAcceptedCount": sift_accepted,
        "phaseAcceptedCount": phase_accepted,
        "sourceConsensusAcceptedEdgeCount": len(consensus_edges),
        "sourceConsensusAcceptedFramePairCount": len(source_consensus_debug),
        "sourceConsensus": source_consensus_debug,
        "sourceTransformPropagationAcceptedEdgeCount": len(propagated_edges),
        "sourceTransformPropagationAcceptedFramePairCount": int(
            sum(item.get("status") == "accepted" for item in source_transform_debug)
        ),
        "sourceTransformPropagation": source_transform_debug,
        "sourceBoundaryConsensusAcceptedEdgeCount": len(boundary_edges),
        "sourceBoundaryConsensusAcceptedFramePairCount": int(
            sum(item.get("status") == "accepted" for item in source_boundary_debug)
        ),
        "sourceBoundaryConsensus": source_boundary_debug,
        "rejectedPairCount": rejected_pairs,
        "phaseEnabled": allow_phase,
        "phaseSkippedBecauseFragmentCountExceeds": (
            None if allow_phase else int(config.fragment_array_phase_max_fragments)
        ),
        "rejectedNonImageSupportedCount": rejected_pairs,
    }
    return strict, tree_edges, raw_poses, components, registration_debug


def _boundary_precision(placed: np.ndarray, target: np.ndarray, tolerance: int) -> float:
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    placed_edge = cv2.morphologyEx((placed > 0).astype(np.uint8), cv2.MORPH_GRADIENT, kernel)
    if int(placed_edge.sum()) == 0:
        return 0.0
    target_edge = cv2.morphologyEx((target > 0).astype(np.uint8), cv2.MORPH_GRADIENT, kernel)
    tolerance_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (2 * tolerance + 1, 2 * tolerance + 1),
    )
    target_dilated = cv2.dilate(target_edge, tolerance_kernel)
    return float(np.count_nonzero(placed_edge & target_dilated)) / max(
        int(np.count_nonzero(placed_edge)), 1
    )


def _response_second_best(
    response: np.ndarray,
    best_location: tuple[int, int],
    template_shape: tuple[int, int],
) -> float:
    if response.size <= 1:
        return float("-inf")
    suppressed = response.copy()
    template_h, template_w = template_shape
    radius_x = max(2, template_w // 4)
    radius_y = max(2, template_h // 4)
    x, y = best_location
    x0 = max(0, x - radius_x)
    x1 = min(suppressed.shape[1], x + radius_x + 1)
    y0 = max(0, y - radius_y)
    y1 = min(suppressed.shape[0], y + radius_y + 1)
    suppressed[y0:y1, x0:x1] = -np.inf
    finite = suppressed[np.isfinite(suppressed)]
    return float(finite.max()) if finite.size else float("-inf")


def _alignment_candidate(
    component_small: np.ndarray,
    target_small: np.ndarray,
    occupied_small: np.ndarray,
    angle_deg: float,
    analysis_scale: float,
    config: AssemblyConfig,
    allowed_small: np.ndarray | None = None,
    minimum_allowed_ratio: float = 0.0,
) -> dict[str, Any] | None:
    rotation, rotated_size = _rotation_bound_matrix(
        component_small.shape[1], component_small.shape[0], angle_deg
    )
    rotated = cv2.warpAffine(
        component_small,
        rotation[:2].astype(np.float32),
        rotated_size,
        flags=cv2.INTER_NEAREST,
    )
    if rotated.shape[0] > target_small.shape[0] or rotated.shape[1] > target_small.shape[1]:
        return None
    template = (rotated > 0).astype(np.float32)
    component_area = max(float(template.sum()), 1.0)
    target_response = cv2.matchTemplate(
        (target_small > 0).astype(np.float32), template, cv2.TM_CCORR
    )
    occupied_weight = float(config.fragment_array_occupied_overlap_weight)
    if occupied_weight > 0.0 and np.any(occupied_small > 0):
        occupied_response = cv2.matchTemplate(
            (occupied_small > 0).astype(np.float32), template, cv2.TM_CCORR
        )
        response = (
            target_response - occupied_weight * occupied_response
        ) / component_area
    else:
        response = target_response / component_area
    allowed_response: np.ndarray | None = None
    if allowed_small is not None:
        allowed_response = cv2.matchTemplate(
            (allowed_small > 0).astype(np.float32),
            template,
            cv2.TM_CCORR,
        ) / component_area
        # Prefer candidates that remain inside their non-overlapping Voronoi
        # search polygon.  Hard-reject only clearly illegal placements; minor
        # silhouette differences between color and X-ray remain tolerated.
        response = response - 0.85 * (1.0 - allowed_response)
        response = response.copy()
        response[allowed_response < float(minimum_allowed_ratio)] = -1.0e9
        if not np.any(response > -1.0e8):
            return None
    _, best_score_small, _, best_location = cv2.minMaxLoc(response)
    second_score_small = _response_second_best(response, best_location, rotated.shape)

    transform_small = _translation(float(best_location[0]), float(best_location[1])) @ rotation
    scale_matrix = np.diag([analysis_scale, analysis_scale, 1.0])
    transform_full = np.linalg.inv(scale_matrix) @ transform_small @ scale_matrix

    # Rank candidates entirely at the bounded analysis resolution. Warping each
    # angle to the full reference canvas can allocate hundreds of megabytes per
    # candidate for large fragment arrays. The selected candidate is materialized
    # once at full resolution in ``_align_bundle_to_array``.
    placed_small = np.zeros_like(target_small, dtype=np.uint8)
    x, y = best_location
    height, width = rotated.shape
    placed_small[y : y + height, x : x + width] = (rotated > 0).astype(np.uint8) * 255
    placed_binary = placed_small > 0
    component_area_small = max(int(np.count_nonzero(placed_binary)), 1)
    inside_ratio = float(
        np.count_nonzero(placed_binary & (target_small > 0))
    ) / component_area_small
    occupied_overlap_ratio = float(
        np.count_nonzero(placed_binary & (occupied_small > 0))
    ) / component_area_small
    search_region_inside_ratio = (
        float(np.count_nonzero(placed_binary & (allowed_small > 0)))
        / component_area_small
        if allowed_small is not None
        else 1.0
    )
    boundary_precision = _boundary_precision(
        placed_small,
        target_small,
        tolerance=max(1, int(round(config.boundary_tolerance_px * analysis_scale))),
    )
    score = (
        0.74 * inside_ratio
        + 0.18 * boundary_precision
        - float(config.fragment_array_occupied_overlap_weight) * occupied_overlap_ratio
    )
    return {
        "transform": transform_full,
        "score": float(score),
        "insideRatio": float(inside_ratio),
        "occupiedOverlapRatio": float(occupied_overlap_ratio),
        "searchRegionInsideRatio": float(search_region_inside_ratio),
        "boundaryPrecision": float(boundary_precision),
        "rotationDeg": float(_normalize_angle(angle_deg)),
        "analysisScale": float(analysis_scale),
        "responseScore": float(best_score_small),
        "responseSecondScore": float(second_score_small),
        "responseMargin": float(best_score_small - second_score_small)
        if math.isfinite(second_score_small)
        else 1.0,
        "locationXY": [int(best_location[0]), int(best_location[1])],
    }


def _warp_mask_to_canvas_bbox(
    mask: np.ndarray,
    transform: np.ndarray,
    canvas_shape: tuple[int, int],
) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    canvas_h, canvas_w = [int(value) for value in canvas_shape]
    height, width = mask.shape
    corners = _transform_points(
        transform,
        np.array(
            [[0.0, 0.0], [width, 0.0], [width, height], [0.0, height]],
            dtype=np.float64,
        ),
    )
    x0 = max(0, int(math.floor(float(corners[:, 0].min()))))
    y0 = max(0, int(math.floor(float(corners[:, 1].min()))))
    x1 = min(canvas_w, int(math.ceil(float(corners[:, 0].max()))))
    y1 = min(canvas_h, int(math.ceil(float(corners[:, 1].max()))))
    if x1 <= x0 or y1 <= y0:
        return np.zeros((0, 0), dtype=np.uint8), (x0, y0, x1, y1)
    local_transform = _translation(-float(x0), -float(y0)) @ transform
    placed = cv2.warpAffine(
        (mask > 0).astype(np.uint8) * 255,
        local_transform[:2].astype(np.float32),
        (x1 - x0, y1 - y0),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    return placed, (x0, y0, x1, y1)


def _align_bundle_to_array(
    component_mask: np.ndarray,
    target: np.ndarray,
    occupied: np.ndarray,
    config: AssemblyConfig,
    *,
    materialize_placed_mask: bool = True,
) -> dict[str, Any]:
    maximum_dimension = max(
        component_mask.shape[0],
        component_mask.shape[1],
        target.shape[0],
        target.shape[1],
    )
    analysis_scale = min(
        1.0,
        float(config.fragment_array_alignment_max_dimension) / max(maximum_dimension, 1),
    )
    if analysis_scale < 0.999:
        component_analysis = cv2.resize(
            component_mask,
            (
                max(1, int(round(component_mask.shape[1] * analysis_scale))),
                max(1, int(round(component_mask.shape[0] * analysis_scale))),
            ),
            interpolation=cv2.INTER_NEAREST,
        )
        target_analysis = cv2.resize(
            target,
            (
                max(1, int(round(target.shape[1] * analysis_scale))),
                max(1, int(round(target.shape[0] * analysis_scale))),
            ),
            interpolation=cv2.INTER_NEAREST,
        )
        occupied_analysis = cv2.resize(
            occupied,
            (target_analysis.shape[1], target_analysis.shape[0]),
            interpolation=cv2.INTER_NEAREST,
        )
    else:
        component_analysis = component_mask
        target_analysis = target
        occupied_analysis = occupied

    if materialize_placed_mask:
        base = _normalize_angle(
            float(cv2.minAreaRect(cv2.findNonZero((target > 0).astype(np.uint8)))[2])
            - float(
                cv2.minAreaRect(
                    cv2.findNonZero((component_mask > 0).astype(np.uint8))
                )[2]
            )
        )
    else:
        # The reference-anchored source-frame route evaluates many acquisitions
        # against one large color canvas.  PCA at the bounded analysis scale is
        # sufficient for angle seeding and avoids scanning the multi-megapixel
        # target once per source frame.
        base = _normalize_angle(
            float(principal_angle_deg(target_analysis))
            - float(principal_angle_deg(component_analysis))
        )
    angles: set[float] = {0.0, 180.0, base, _normalize_angle(base + 180.0)}
    full_step = int(config.fragment_array_full_rotation_step_deg)
    if full_step > 0:
        angles.update(float(angle) for angle in range(-180, 180, full_step))

    candidates = [
        candidate
        for angle in sorted(angles)
        if (
            candidate := _alignment_candidate(
                component_analysis,
                target_analysis,
                occupied_analysis,
                angle,
                analysis_scale,
                config,
            )
        )
        is not None
    ]
    if not candidates:
        raise RuntimeError("파편 배열 component를 reference canvas에 배치할 후보가 없습니다.")
    coarse_best = max(candidates, key=lambda item: item["score"])
    coarse_angle = float(coarse_best["rotationDeg"])
    fine_radius = max(1.0, float(config.fragment_array_full_rotation_step_deg) / 2.0)
    fine_step = max(0.5, float(config.global_refine_angle_step_deg))
    fine_angles = np.arange(
        coarse_angle - fine_radius,
        coarse_angle + fine_radius + 0.01,
        fine_step,
    )
    for angle in fine_angles:
        candidate = _alignment_candidate(
            component_analysis,
            target_analysis,
            occupied_analysis,
            float(angle),
            analysis_scale,
            config,
        )
        if candidate is not None:
            candidates.append(candidate)
    candidates.sort(key=lambda item: item["score"], reverse=True)
    best = dict(candidates[0])
    alternative_score = candidates[1]["score"] if len(candidates) > 1 else -1.0
    best["candidateScoreMargin"] = float(best["score"] - alternative_score)
    best["candidateCount"] = len(candidates)
    best["analysisScore"] = float(best["score"])
    best["analysisInsideRatio"] = float(best["insideRatio"])
    best["analysisOccupiedOverlapRatio"] = float(best["occupiedOverlapRatio"])
    best["analysisBoundaryPrecision"] = float(best["boundaryPrecision"])

    placed_local, (x0, y0, x1, y1) = _warp_mask_to_canvas_bbox(
        component_mask,
        best["transform"],
        target.shape,
    )
    placed_binary = placed_local > 0
    component_area_full = max(int(np.count_nonzero(placed_binary)), 1)
    if placed_local.size:
        target_crop = target[y0:y1, x0:x1]
        occupied_crop = occupied[y0:y1, x0:x1]
        inside_ratio = float(
            np.count_nonzero(placed_binary & (target_crop > 0))
        ) / component_area_full
        occupied_overlap_ratio = float(
            np.count_nonzero(placed_binary & (occupied_crop > 0))
        ) / component_area_full
        boundary_precision = _boundary_precision(
            placed_local,
            target_crop,
            tolerance=max(1, int(config.boundary_tolerance_px)),
        )
    else:
        inside_ratio = 0.0
        occupied_overlap_ratio = 0.0
        boundary_precision = 0.0
    placed_full: np.ndarray | None = None
    if materialize_placed_mask:
        placed_full = np.zeros_like(target, dtype=np.uint8)
        if placed_local.size:
            placed_full[y0:y1, x0:x1] = placed_local
    best.update(
        {
            "placedMask": placed_full,
            "placedBBoxXYXY": [int(x0), int(y0), int(x1), int(y1)],
            "insideRatio": inside_ratio,
            "occupiedOverlapRatio": occupied_overlap_ratio,
            "boundaryPrecision": boundary_precision,
            "score": float(
                0.74 * inside_ratio
                + 0.18 * boundary_precision
                - float(config.fragment_array_occupied_overlap_weight)
                * occupied_overlap_ratio
            ),
        }
    )
    return best


def _reference_slot_assignments(
    placed_mask: np.ndarray,
    reference_records: list[dict[str, Any]],
    config: AssemblyConfig,
) -> list[dict[str, Any]]:
    placed = placed_mask > 0
    assignments: list[dict[str, Any]] = []
    for slot_index, record in enumerate(reference_records):
        x, y, width, height = record["bbox"]
        placed_crop = placed[y : y + height, x : x + width]
        slot = record["mask"] > 0
        intersection = int(np.count_nonzero(placed_crop & slot))
        if intersection <= 0:
            continue
        slot_coverage = intersection / max(int(record["area"]), 1)
        if slot_coverage < float(config.fragment_array_min_slot_coverage):
            continue
        assignments.append(
            {
                "referenceSlotIndex": slot_index,
                "intersectionPx": intersection,
                "slotCoverage": float(slot_coverage),
            }
        )
    assignments.sort(key=lambda item: item["referenceSlotIndex"])
    return assignments


def _mask_primary_contour(mask: np.ndarray) -> np.ndarray | None:
    contours, _ = cv2.findContours(
        (mask > 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return None
    return max(contours, key=cv2.contourArea)


def _mask_centroid(mask: np.ndarray) -> tuple[float, float]:
    moments = cv2.moments((mask > 0).astype(np.uint8))
    if abs(float(moments["m00"])) < 1e-6:
        height, width = mask.shape[:2]
        return float((width - 1) / 2.0), float((height - 1) / 2.0)
    return (
        float(moments["m10"]) / float(moments["m00"]),
        float(moments["m01"]) / float(moments["m00"]),
    )


def _reference_slot_features(slot_index: int, record: dict[str, Any]) -> dict[str, Any]:
    mask = record["mask"]
    contour = _mask_primary_contour(mask)
    bbox = tuple(int(value) for value in record["bbox"])
    _, _, width, height = bbox
    cx_local, cy_local = _mask_centroid(mask)
    return {
        **record,
        "slotIndex": int(slot_index),
        "mask": mask,
        "contour": contour,
        "aspect": float(width / max(height, 1)),
        "angle": float(principal_angle_deg(mask)),
        "compactness": _shape_compactness(mask, contour),
        "solidity": _shape_solidity(mask, contour),
        "centroidLocalXY": [float(cx_local), float(cy_local)],
        "centroidGlobalXY": [float(bbox[0] + cx_local), float(bbox[1] + cy_local)],
    }


def _bundle_shape_features(bundle: dict[str, Any]) -> dict[str, Any]:
    mask = bundle["mask"]
    contour = _mask_primary_contour(mask)
    bbox = cv2.boundingRect((mask > 0).astype(np.uint8))
    _, _, width, height = bbox
    cx_local, cy_local = _mask_centroid(mask)
    return {
        "mask": mask,
        "contour": contour,
        "area": int(bundle["area"]),
        "aspect": float(width / max(height, 1)),
        "angle": float(bundle.get("angle", principal_angle_deg(mask))),
        "compactness": _shape_compactness(mask, contour),
        "solidity": _shape_solidity(mask, contour),
        "bbox": tuple(int(value) for value in bbox),
        "centroidLocalXY": [float(cx_local), float(cy_local)],
    }


def _shape_compactness(mask: np.ndarray, contour: np.ndarray | None) -> float:
    if contour is None:
        return 0.0
    perimeter = float(cv2.arcLength(contour, True))
    area = float(np.count_nonzero(mask))
    if perimeter <= 1e-6 or area <= 0:
        return 0.0
    return float(4.0 * math.pi * area / (perimeter * perimeter))


def _shape_solidity(mask: np.ndarray, contour: np.ndarray | None) -> float:
    if contour is None:
        return 0.0
    hull = cv2.convexHull(contour)
    hull_area = float(cv2.contourArea(hull))
    area = float(np.count_nonzero(mask))
    if hull_area <= 1e-6:
        return 0.0
    return float(np.clip(area / hull_area, 0.0, 1.0))


def _fit_binary_mask(mask: np.ndarray, size: int = 96, padding: int = 5) -> np.ndarray:
    binary = (mask > 0).astype(np.uint8) * 255
    points = cv2.findNonZero(binary)
    if points is None:
        return np.zeros((size, size), dtype=np.uint8)
    x, y, width, height = cv2.boundingRect(points)
    crop = binary[y : y + height, x : x + width]
    available = max(1, size - 2 * padding)
    scale = min(available / max(width, 1), available / max(height, 1))
    resized = cv2.resize(
        crop,
        (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
        interpolation=cv2.INTER_NEAREST,
    )
    canvas = np.zeros((size, size), dtype=np.uint8)
    x0 = (size - resized.shape[1]) // 2
    y0 = (size - resized.shape[0]) // 2
    canvas[y0 : y0 + resized.shape[0], x0 : x0 + resized.shape[1]] = resized
    return canvas


def _canonical_shape_mask(mask: np.ndarray, size: int = 96) -> np.ndarray:
    binary = (mask > 0).astype(np.uint8) * 255
    points = cv2.findNonZero(binary)
    if points is None:
        return np.zeros((size, size), dtype=np.uint8)
    x, y, width, height = cv2.boundingRect(points)
    crop = binary[y : y + height, x : x + width]
    # Canonical descriptors do not need acquisition resolution.  Downsample
    # before rotation so very large X-ray component canvases do not dominate
    # runtime or memory.
    descriptor_limit = max(size * 2, 160)
    scale = min(1.0, descriptor_limit / max(crop.shape))
    if scale < 0.999:
        crop = cv2.resize(
            crop,
            (
                max(1, int(round(crop.shape[1] * scale))),
                max(1, int(round(crop.shape[0] * scale))),
            ),
            interpolation=cv2.INTER_NEAREST,
        )
    angle = float(principal_angle_deg(crop))
    aligned = rotate_bound(
        crop,
        -angle,
        interpolation=cv2.INTER_NEAREST,
        border_value=0,
    )
    return _fit_binary_mask(aligned, size=size)


def _symmetric_chamfer_distance(first: np.ndarray, second: np.ndarray) -> float:
    first_binary = first > 0
    second_binary = second > 0
    if not np.any(first_binary) or not np.any(second_binary):
        return float("inf")
    first_edge = cv2.morphologyEx(
        first_binary.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)
    ) > 0
    second_edge = cv2.morphologyEx(
        second_binary.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)
    ) > 0
    if not np.any(first_edge) or not np.any(second_edge):
        return float("inf")
    distance_to_first = cv2.distanceTransform((~first_edge).astype(np.uint8), cv2.DIST_L2, 3)
    distance_to_second = cv2.distanceTransform((~second_edge).astype(np.uint8), cv2.DIST_L2, 3)
    forward = float(distance_to_second[first_edge].mean())
    backward = float(distance_to_first[second_edge].mean())
    return float((forward + backward) / (2.0 * max(first.shape)))


def _canonical_chamfer_distance(first_mask: np.ndarray, second_mask: np.ndarray) -> float:
    first = _canonical_shape_mask(first_mask)
    second = _canonical_shape_mask(second_mask)
    direct = _symmetric_chamfer_distance(first, second)
    reversed_axis = _symmetric_chamfer_distance(cv2.rotate(first, cv2.ROTATE_180), second)
    return float(min(direct, reversed_axis))


def _shape_cost_between_bundle_and_slot(
    bundle_features: dict[str, Any],
    slot_features: dict[str, Any],
    *,
    include_area: bool = True,
    chamfer_weight: float = 0.0,
) -> tuple[float, dict[str, float]]:
    bundle_contour = bundle_features.get("contour")
    slot_contour = slot_features.get("contour")
    if bundle_contour is None or slot_contour is None:
        return float("inf"), {}
    hu_distance = float(
        cv2.matchShapes(
            bundle_contour,
            slot_contour,
            cv2.CONTOURS_MATCH_I1,
            0.0,
        )
    )
    if not np.isfinite(hu_distance):
        return float("inf"), {}
    bundle_aspect = max(float(bundle_features["aspect"]), 1.0 / max(float(bundle_features["aspect"]), 1e-6))
    slot_aspect = max(float(slot_features["aspect"]), 1.0 / max(float(slot_features["aspect"]), 1e-6))
    aspect_ratio = max(bundle_aspect, slot_aspect) / max(1e-6, min(bundle_aspect, slot_aspect))
    compactness_delta = abs(
        float(bundle_features.get("compactness", 0.0))
        - float(slot_features.get("compactness", 0.0))
    )
    solidity_delta = abs(
        float(bundle_features.get("solidity", 0.0))
        - float(slot_features.get("solidity", 0.0))
    )
    area_penalty = 0.0
    if include_area:
        area_ratio = max(bundle_features["area"], slot_features["area"]) / max(
            1.0, min(bundle_features["area"], slot_features["area"])
        )
        area_penalty = abs(math.log(max(area_ratio, 1e-6)))
    chamfer = 0.0
    if chamfer_weight > 0.0:
        first_canonical = bundle_features.get("canonicalMask")
        if first_canonical is None:
            first_canonical = _canonical_shape_mask(bundle_features["mask"])
            bundle_features["canonicalMask"] = first_canonical
        second_canonical = slot_features.get("canonicalMask")
        if second_canonical is None:
            second_canonical = _canonical_shape_mask(slot_features["mask"])
            slot_features["canonicalMask"] = second_canonical
        chamfer = min(
            _symmetric_chamfer_distance(first_canonical, second_canonical),
            _symmetric_chamfer_distance(
                cv2.rotate(first_canonical, cv2.ROTATE_180),
                second_canonical,
            ),
        )
        if not np.isfinite(chamfer):
            return float("inf"), {}
    weight = float(np.clip(chamfer_weight, 0.0, 0.60))
    base = (
        0.62 * min(hu_distance, 1.5)
        + 0.14 * abs(math.log(max(aspect_ratio, 1e-6)))
        + 0.08 * compactness_delta
        + 0.06 * solidity_delta
        + (0.10 * area_penalty if include_area else 0.0)
    )
    cost = (1.0 - weight) * base + weight * min(chamfer * 4.0, 1.5)
    return float(cost), {
        "huDistance": float(hu_distance),
        "aspectPenalty": float(abs(math.log(max(aspect_ratio, 1e-6)))),
        "compactnessDelta": float(compactness_delta),
        "solidityDelta": float(solidity_delta),
        "areaPenalty": float(area_penalty),
        "chamferDistance": float(chamfer),
    }


def _robust_median(values: list[float]) -> tuple[float | None, list[float]]:
    finite = np.asarray([value for value in values if np.isfinite(value) and value > 0], dtype=np.float64)
    if finite.size == 0:
        return None, []
    median = float(np.median(finite))
    deviations = np.abs(finite - median)
    mad = float(np.median(deviations))
    if mad <= 1e-9:
        kept = finite
    else:
        kept = finite[deviations <= 2.5 * 1.4826 * mad]
        if kept.size == 0:
            kept = finite
    return float(np.median(kept)), [float(value) for value in kept.tolist()]


def _estimate_reference_scale_from_shape_anchors(
    reference_mask: np.ndarray,
    bundles: list[dict[str, Any]],
    config: AssemblyConfig,
) -> tuple[float | None, dict[str, Any]]:
    reference_records = _compact_reference_slot_records(
        reference_mask,
        minimum_area_ratio=float(config.fragment_array_reference_min_component_area_ratio),
        config=config,
    )
    if not reference_records or not bundles:
        return None, {"method": "shape_anchor_scale", "reason": "empty_records"}
    slot_features = [
        _reference_slot_features(index, record)
        for index, record in enumerate(reference_records)
    ]
    bundle_features = [_bundle_shape_features(bundle) for bundle in bundles]
    anchor_limit = max(3, int(config.fragment_array_color_slot_anchor_count))
    candidate_bundle_indices = sorted(
        range(len(bundles)), key=lambda index: int(bundles[index]["area"]), reverse=True
    )[: max(anchor_limit * 2, anchor_limit)]
    costs: dict[tuple[int, int], float] = {}
    for bundle_index in candidate_bundle_indices:
        for slot_index, slot in enumerate(slot_features):
            cost, _ = _shape_cost_between_bundle_and_slot(
                bundle_features[bundle_index],
                slot,
                include_area=False,
                chamfer_weight=0.0,
            )
            costs[(bundle_index, slot_index)] = float(cost)
    best_slot_for_bundle: dict[int, tuple[int, float]] = {}
    for bundle_index in candidate_bundle_indices:
        ranked = sorted(
            ((costs[(bundle_index, slot_index)], slot_index) for slot_index in range(len(slot_features))),
            key=lambda item: (item[0], item[1]),
        )
        if ranked:
            best_slot_for_bundle[bundle_index] = (int(ranked[0][1]), float(ranked[0][0]))
    best_bundle_for_slot: dict[int, tuple[int, float]] = {}
    for slot_index in range(len(slot_features)):
        ranked = sorted(
            ((costs[(bundle_index, slot_index)], bundle_index) for bundle_index in candidate_bundle_indices),
            key=lambda item: (item[0], item[1]),
        )
        if ranked:
            best_bundle_for_slot[slot_index] = (int(ranked[0][1]), float(ranked[0][0]))
    anchors: list[dict[str, Any]] = []
    maximum_cost = float(config.fragment_array_color_slot_anchor_max_shape_cost)
    for bundle_index, (slot_index, cost) in best_slot_for_bundle.items():
        reverse = best_bundle_for_slot.get(slot_index)
        if reverse is None or int(reverse[0]) != int(bundle_index) or cost > maximum_cost:
            continue
        bundle_area = max(float(bundle_features[bundle_index]["area"]), 1.0)
        slot_area = max(float(slot_features[slot_index]["area"]), 1.0)
        scale = math.sqrt(bundle_area / slot_area)
        anchors.append(
            {
                "xrayComponentIndex": int(bundle_index),
                "referenceSlotIndex": int(slot_index),
                "shapeCost": float(cost),
                "scale": float(scale),
            }
        )
    anchors.sort(key=lambda item: (float(item["shapeCost"]), -float(bundles[item["xrayComponentIndex"]]["area"])))
    anchors = anchors[:anchor_limit]
    selected, kept_scales = _robust_median([float(item["scale"]) for item in anchors])
    if selected is None or len(kept_scales) < 2:
        return None, {
            "method": "shape_anchor_scale",
            "reason": "insufficient_mutual_anchors",
            "anchorCount": len(anchors),
            "anchors": anchors,
        }
    maximum_reference_dimension = int(config.fragment_array_reference_max_dimension)
    cap = (
        float(maximum_reference_dimension) / max(reference_mask.shape)
        if maximum_reference_dimension > 0
        else float("inf")
    )
    selected = max(1e-3, min(float(selected), float(cap)))
    return selected, {
        "method": "mutual_shape_anchor_median",
        "selectedScale": float(selected),
        "anchorCount": len(anchors),
        "keptScaleCount": len(kept_scales),
        "keptScales": kept_scales,
        "anchors": anchors,
        "dimensionCapScale": float(cap),
    }


def _align_bundle_to_reference_slot(
    component_mask: np.ndarray,
    slot_record: dict[str, Any],
    occupied: np.ndarray,
    config: AssemblyConfig,
) -> dict[str, Any] | None:
    slot_x, slot_y, slot_width, slot_height = [int(value) for value in slot_record["bbox"]]
    use_voronoi = bool(
        config.fragment_array_color_slot_voronoi_search_enabled
        and slot_record.get("searchMask") is not None
        and slot_record.get("searchBBox") is not None
    )
    if use_voronoi:
        crop_x0, crop_y0, crop_width, crop_height = [
            int(value) for value in slot_record["searchBBox"]
        ]
        crop_x1 = crop_x0 + crop_width
        crop_y1 = crop_y0 + crop_height
        allowed_crop = (slot_record["searchMask"] > 0).astype(np.uint8) * 255
    else:
        padding = max(
            10,
            int(
                round(
                    max(component_mask.shape[0], component_mask.shape[1], slot_width, slot_height)
                    * float(config.fragment_array_color_slot_search_padding_ratio)
                )
            ),
        )
        crop_x0 = max(0, slot_x - padding)
        crop_y0 = max(0, slot_y - padding)
        crop_x1 = min(occupied.shape[1], slot_x + slot_width + padding)
        crop_y1 = min(occupied.shape[0], slot_y + slot_height + padding)
        crop_width = max(1, crop_x1 - crop_x0)
        crop_height = max(1, crop_y1 - crop_y0)
        allowed_crop = np.ones((crop_height, crop_width), dtype=np.uint8) * 255
    target_crop = np.zeros((crop_height, crop_width), dtype=np.uint8)
    target_crop[
        slot_y - crop_y0 : slot_y - crop_y0 + slot_height,
        slot_x - crop_x0 : slot_x - crop_x0 + slot_width,
    ] = slot_record["mask"]
    occupied_crop = occupied[crop_y0:crop_y1, crop_x0:crop_x1]

    maximum_dimension = max(
        component_mask.shape[0],
        component_mask.shape[1],
        target_crop.shape[0],
        target_crop.shape[1],
    )
    analysis_scale = min(
        1.0,
        float(config.fragment_array_alignment_max_dimension) / max(maximum_dimension, 1),
    )
    if analysis_scale < 0.999:
        component_analysis = cv2.resize(
            component_mask,
            (
                max(1, int(round(component_mask.shape[1] * analysis_scale))),
                max(1, int(round(component_mask.shape[0] * analysis_scale))),
            ),
            interpolation=cv2.INTER_NEAREST,
        )
        target_analysis = cv2.resize(
            target_crop,
            (
                max(1, int(round(target_crop.shape[1] * analysis_scale))),
                max(1, int(round(target_crop.shape[0] * analysis_scale))),
            ),
            interpolation=cv2.INTER_NEAREST,
        )
        occupied_analysis = cv2.resize(
            occupied_crop,
            (target_analysis.shape[1], target_analysis.shape[0]),
            interpolation=cv2.INTER_NEAREST,
        )
        allowed_analysis = cv2.resize(
            allowed_crop,
            (target_analysis.shape[1], target_analysis.shape[0]),
            interpolation=cv2.INTER_NEAREST,
        )
    else:
        component_analysis = component_mask
        target_analysis = target_crop
        occupied_analysis = occupied_crop
        allowed_analysis = allowed_crop

    base = _normalize_angle(
        float(principal_angle_deg(target_crop)) - float(principal_angle_deg(component_mask))
    )
    # The color slot already supplies a specific object shape and PCA axis, so
    # an exhaustive -180..180 search is unnecessary and prohibitively expensive
    # for arrays with dozens of fragments.  Test the two axis directions plus a
    # small local angular neighborhood; nearly round slots also retain 0/180.
    coarse_radius = max(
        6.0,
        min(18.0, float(config.fragment_array_full_rotation_step_deg) / 2.0),
    )
    angles: set[float] = {
        base,
        _normalize_angle(base + 180.0),
        _normalize_angle(base - coarse_radius),
        _normalize_angle(base + coarse_radius),
        _normalize_angle(base + 180.0 - coarse_radius),
        _normalize_angle(base + 180.0 + coarse_radius),
        0.0,
        180.0,
    }

    candidates = [
        candidate
        for angle in sorted(angles)
        if (
            candidate := _alignment_candidate(
                component_analysis,
                target_analysis,
                occupied_analysis,
                angle,
                analysis_scale,
                config,
                allowed_small=allowed_analysis if use_voronoi else None,
                minimum_allowed_ratio=(
                    float(config.fragment_array_color_slot_voronoi_min_inside_ratio)
                    if use_voronoi
                    else 0.0
                ),
            )
        )
        is not None
    ]
    if not candidates:
        return None
    coarse_best = max(candidates, key=lambda item: item["score"])
    coarse_angle = float(coarse_best["rotationDeg"])
    fine_radius = max(3.0, min(10.0, coarse_radius))
    fine_step = max(2.0, float(config.global_refine_angle_step_deg))
    for angle in np.arange(coarse_angle - fine_radius, coarse_angle + fine_radius + 0.01, fine_step):
        candidate = _alignment_candidate(
            component_analysis,
            target_analysis,
            occupied_analysis,
            float(angle),
            analysis_scale,
            config,
            allowed_small=allowed_analysis if use_voronoi else None,
            minimum_allowed_ratio=(
                float(config.fragment_array_color_slot_voronoi_min_inside_ratio)
                if use_voronoi
                else 0.0
            ),
        )
        if candidate is not None:
            candidates.append(candidate)
    candidates.sort(key=lambda item: item["score"], reverse=True)
    best = dict(candidates[0])
    alternative_score = candidates[1]["score"] if len(candidates) > 1 else -1.0
    best["candidateScoreMargin"] = float(best["score"] - alternative_score)
    best["candidateCount"] = len(candidates)
    best["transform"] = _translation(float(crop_x0), float(crop_y0)) @ best["transform"]

    placed = cv2.warpAffine(
        component_mask,
        best["transform"][:2].astype(np.float32),
        (occupied.shape[1], occupied.shape[0]),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    placed_binary = placed > 0
    component_area_full = max(int(np.count_nonzero(placed_binary)), 1)
    slot_full = np.zeros_like(occupied, dtype=np.uint8)
    slot_full[slot_y : slot_y + slot_height, slot_x : slot_x + slot_width] = slot_record["mask"]
    slot_binary = slot_full > 0
    search_full = np.zeros_like(occupied, dtype=np.uint8)
    if use_voronoi:
        search_full[crop_y0:crop_y1, crop_x0:crop_x1] = allowed_crop
    else:
        search_full[crop_y0:crop_y1, crop_x0:crop_x1] = 255
    search_region_inside_ratio = float(
        np.count_nonzero(placed_binary & (search_full > 0))
    ) / component_area_full
    intersection = int(np.count_nonzero(placed_binary & slot_binary))
    inside_ratio = intersection / component_area_full
    slot_coverage = intersection / max(int(slot_record["area"]), 1)
    occupied_overlap_ratio = float(
        np.count_nonzero(placed_binary & (occupied > 0))
    ) / component_area_full
    boundary_precision = _boundary_precision(
        placed, slot_full, tolerance=max(1, int(config.boundary_tolerance_px))
    )
    best.update(
        {
            "placedMask": placed,
            "insideRatio": float(inside_ratio),
            "slotCoverage": float(slot_coverage),
            "intersectionPx": int(intersection),
            "occupiedOverlapRatio": float(occupied_overlap_ratio),
            "searchRegionInsideRatio": float(search_region_inside_ratio),
            "searchRegionMethod": (
                "nearest_fragment_voronoi_clipped_by_expansion"
                if use_voronoi
                else "rectangular_padding"
            ),
            "searchPolygonXY": slot_record.get("searchPolygonXY", []),
            "boundaryPrecision": float(boundary_precision),
            "score": float(
                0.58 * slot_coverage
                + 0.22 * inside_ratio
                + 0.16 * boundary_precision
                - float(config.fragment_array_occupied_overlap_weight)
                * occupied_overlap_ratio
            ),
        }
    )
    return best


def _shelf_pack_neutral(
    records: list[dict[str, Any]],
    target_width: int,
    margin: int,
    gap: int,
) -> tuple[list[dict[str, Any]], int, int]:
    x = margin
    y = margin
    row_height = 0
    used_width = margin
    placements: list[dict[str, Any]] = []
    for record in records:
        width = int(record["width"])
        height = int(record["height"])
        if x > margin and x + width + margin > target_width:
            x = margin
            y += row_height + gap
            row_height = 0
        placements.append({**record, "x": int(x), "y": int(y)})
        x += width + gap
        row_height = max(row_height, height)
        used_width = max(used_width, x - gap + margin)
    return placements, int(used_width), int(y + row_height + margin)


def _select_neutral_packing(
    records: list[dict[str, Any]], config: AssemblyConfig
) -> tuple[list[dict[str, Any]], tuple[int, int], dict[str, Any]]:
    margin = max(0, int(config.fragment_array_packing_margin_px))
    gap = max(0, int(config.fragment_array_packing_gap_px))
    maximum = int(config.max_output_dimension)
    largest_width = max(int(record["width"]) for record in records)
    padded_area = sum(
        (int(record["width"]) + gap) * (int(record["height"]) + gap)
        for record in records
    )
    explicit = int(config.fragment_array_packing_target_width_px)
    if explicit > 0:
        widths = [max(explicit, largest_width + 2 * margin)]
    else:
        base = max(
            largest_width + 2 * margin,
            int(math.ceil(math.sqrt(max(padded_area, 1)))),
        )
        widths = sorted(
            {
                max(largest_width + 2 * margin, int(round(base * factor)))
                for factor in (0.85, 1.0, 1.2, 1.45, 1.75, 2.1)
            }
        )
    candidates: list[tuple[float, list[dict[str, Any]], int, int, int]] = []
    for width in widths:
        width = min(width, maximum) if maximum > 0 else width
        if width < largest_width + 2 * margin:
            continue
        packed, used_width, used_height = _shelf_pack_neutral(
            records, width, margin, gap
        )
        if maximum > 0 and (used_width > maximum or used_height > maximum):
            continue
        aspect_penalty = abs(math.log(max(used_width, 1) / max(used_height, 1)))
        score = max(used_width, used_height) + 0.12 * min(
            used_width, used_height
        ) + 20.0 * aspect_penalty
        candidates.append((score, packed, used_width, used_height, width))
    if not candidates:
        raise RuntimeError(
            "파편 배열 neutral packing canvas가 max_output_dimension을 초과했습니다."
        )
    _, packed, canvas_w, canvas_h, selected_width = min(
        candidates, key=lambda item: item[0]
    )
    return packed, (canvas_w, canvas_h), {
        "strategy": "area_descending_compact_shelf_without_semantic_order",
        "marginPx": margin,
        "gapPx": gap,
        "selectedShelfWidthPx": int(selected_width),
        "candidateShelfWidthsPx": widths,
    }


def neutral_pack_fragment_array_components(
    fragments: list[Fragment],
    raw_poses: list[np.ndarray],
    components: list[list[int]],
    tree_edges: list[PairRegistration],
    config: AssemblyConfig,
) -> tuple[
    list[np.ndarray],
    SearchGeometry,
    dict[str, Any],
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
]:
    bundles = [
        _component_local_bundle(fragments, raw_poses, component)
        for component in components
    ]
    records: list[dict[str, Any]] = []
    for component_index, bundle in enumerate(bundles):
        bx, by, width, height = [int(value) for value in bundle["bbox"]]
        records.append(
            {
                "componentIndex": component_index,
                "bundle": bundle,
                "bboxX": bx,
                "bboxY": by,
                "width": max(width, 1),
                "height": max(height, 1),
                "area": int(bundle["area"]),
                "firstSourceIndex": min(bundle["indices"]),
            }
        )
    records.sort(
        key=lambda item: (
            -int(item["area"]),
            -max(int(item["width"]), int(item["height"])),
            int(item["firstSourceIndex"]),
        )
    )
    packed, canvas_size, packing_debug = _select_neutral_packing(records, config)
    canvas_w, canvas_h = canvas_size
    final_poses: list[np.ndarray | None] = [None] * len(fragments)
    component_occupancy = np.zeros((canvas_h, canvas_w), dtype=np.uint16)
    reports_by_index: dict[int, dict[str, Any]] = {}

    for packing_order, record in enumerate(packed):
        component_index = int(record["componentIndex"])
        bundle = record["bundle"]
        shift = _translation(
            float(record["x"] - record["bboxX"]),
            float(record["y"] - record["bboxY"]),
        )
        for fragment_index in bundle["indices"]:
            final_poses[fragment_index] = shift @ bundle["poses"][fragment_index]
        placed = cv2.warpAffine(
            (bundle["mask"] > 0).astype(np.uint8),
            shift[:2].astype(np.float32),
            canvas_size,
            flags=cv2.INTER_NEAREST,
        ) > 0
        component_occupancy[placed] += 1
        index_set = set(bundle["indices"])
        internal_edges = [
            edge
            for edge in tree_edges
            if edge.first_index in index_set and edge.second_index in index_set
        ]
        source_count = len(bundle["indices"])
        review_required = bool(
            len(internal_edges) < max(0, source_count - 1)
            or any(edge.review_required for edge in internal_edges)
        )
        reports_by_index[component_index] = {
            "xrayComponentIndex": component_index,
            "fragmentIndices": [int(index) for index in bundle["indices"]],
            "sourceImageCount": source_count,
            "status": (
                "same_fragment_scans_consolidated_neutral_packed"
                if source_count > 1
                else "independent_fragment_preserved_neutral_packed"
            ),
            "componentRole": (
                "consolidated_same_physical_fragment"
                if source_count > 1
                else "independent_fragment"
            ),
            "referenceRole": "not_used_for_position_or_order",
            "packingOrder": packing_order,
            "packingBBoxXYWH": [
                int(record["x"]),
                int(record["y"]),
                int(record["width"]),
                int(record["height"]),
            ],
            "selectedInternalRegistrationCount": len(internal_edges),
            "selectedInternalRegistrationMethods": sorted(
                {edge.method for edge in internal_edges}
            ),
            "minimumInternalRegistrationScore": (
                float(min(edge.score for edge in internal_edges))
                if internal_edges
                else None
            ),
            "reviewRequired": review_required,
        }
        residual_flags = [
            bool(fragments[index].diagnostics.get("residualNoiseGroup", False))
            for index in bundle["indices"]
        ]
        if residual_flags and all(residual_flags):
            reports_by_index[component_index].update(
                {
                    "status": "source_residual_pixels_preserved_neutral_packed",
                    "componentRole": "residual_pixels_not_counted_as_physical_fragment",
                    "reviewRequired": False,
                }
            )

    resolved_poses = [
        pose if pose is not None else np.eye(3, dtype=np.float64)
        for pose in final_poses
    ]
    final_mosaic, final_mask, final_counts = _render_mosaic_local(
        fragments, resolved_poses, canvas_size
    )
    component_reports = [reports_by_index[index] for index in range(len(bundles))]
    diagnostic = normalize_preview(final_mosaic)
    for component_report in component_reports:
        x, y, width, height = component_report["packingBBoxXYWH"]
        cv2.rectangle(
            diagnostic,
            (x, y),
            (x + width - 1, y + height - 1),
            (255, 255, 255),
            2,
        )
        cv2.putText(
            diagnostic,
            f"C{component_report['xrayComponentIndex']:02d} n={component_report['sourceImageCount']}",
            (x + 4, max(14, y + 16)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )

    inter_component_overlap = int(np.count_nonzero(component_occupancy > 1))
    consolidated_count = sum(len(component) > 1 for component in components)
    independent_count = len(components) - consolidated_count
    residual_fragment_indices = {
        int(fragment.index)
        for fragment in fragments
        if bool(fragment.diagnostics.get("residualNoiseGroup", False))
    }
    residual_component_count = sum(
        bool(component)
        and all(int(index) in residual_fragment_indices for index in component)
        for component in components
    )
    physical_observation_count = len(fragments) - len(residual_fragment_indices)
    physical_component_count = len(components) - residual_component_count
    ambiguous_count = sum(
        bool(component_report["reviewRequired"])
        for component_report in component_reports
    )
    assembly_area = int(np.count_nonzero(final_mask))
    search_ratio = min(1.0, config.search_max_dimension / max(canvas_w, canvas_h))
    search_size = (
        max(32, int(round(canvas_w * search_ratio))),
        max(32, int(round(canvas_h * search_ratio))),
    )
    geometry = SearchGeometry(
        target_mask_full=final_mask.copy(),
        target_preview_full=diagnostic.copy(),
        target_mask_opt=cv2.resize(
            final_mask, search_size, interpolation=cv2.INTER_NEAREST
        ),
        search_scale_x=search_size[0] / canvas_w,
        search_scale_y=search_size[1] / canvas_h,
        canvas_width_full=canvas_w,
        canvas_height_full=canvas_h,
        reference_scale=1.0,
        target_offset_xy=(0, 0),
    )
    report = {
        "score": float(1.0 if inter_component_overlap == 0 else 0.0),
        "iou": None,
        "boundaryF1": None,
        "xrayComponentCount": len(bundles),
        "assignedComponentCount": len(bundles),
        "referenceComponentCount": None,
        "referenceAssignmentPerformed": False,
        "components": component_reports,
        "neutralPackingMetrics": {
            "primaryMetric": "component_consolidation_and_collision_free_neutral_packing",
            "packedComponentCount": len(bundles),
            "placementUnitCount": len(fragments),
            "physicalFragmentObservationCount": int(physical_observation_count),
            "physicalFragmentComponentCount": int(physical_component_count),
            "residualPlacementUnitCount": len(residual_fragment_indices),
            "residualComponentCount": int(residual_component_count),
            "consolidatedPhysicalObservationCount": int(
                physical_observation_count - physical_component_count
            ),
            "consolidatedComponentCount": int(consolidated_count),
            "independentComponentCount": int(independent_count),
            "sourceImageCountInConsolidatedComponents": int(
                sum(len(component) for component in components if len(component) > 1)
            ),
            "sourceImageCountPreservedIndependently": int(independent_count),
            "unassignedXrayComponentCount": 0,
            "ambiguousConsolidatedComponentCount": int(ambiguous_count),
            "interComponentOverlapPixels": inter_component_overlap,
            "sourceFrameOverlapPixelsWithinComponents": int(
                np.maximum(final_counts.astype(np.int32) - 1, 0).sum()
            ),
            "canvasUtilizationRatio": float(
                assembly_area / max(canvas_w * canvas_h, 1)
            ),
            "semanticOrderApplied": False,
            "colorReferenceAssignmentPerformed": False,
            "packing": packing_debug,
        },
    }
    return (
        resolved_poses,
        geometry,
        report,
        final_mosaic,
        final_mask,
        final_counts,
        diagnostic,
    )


def _minimum_cost_row_assignment(cost_matrix: np.ndarray) -> list[int]:
    """Hungarian assignment for n rows and m columns where n <= m.

    Returns one selected column per row.  Callers may append one zero-cost dummy
    column per row to permit explicit unassignment without a third-party solver.
    """
    if cost_matrix.ndim != 2:
        raise ValueError("cost_matrix는 2차원이어야 합니다.")
    row_count, column_count = cost_matrix.shape
    if row_count == 0:
        return []
    if row_count > column_count:
        raise ValueError("Hungarian assignment는 row_count <= column_count가 필요합니다.")
    costs = np.asarray(cost_matrix, dtype=np.float64)
    u = np.zeros(row_count + 1, dtype=np.float64)
    v = np.zeros(column_count + 1, dtype=np.float64)
    p = np.zeros(column_count + 1, dtype=np.int32)
    way = np.zeros(column_count + 1, dtype=np.int32)
    for row in range(1, row_count + 1):
        p[0] = row
        min_values = np.full(column_count + 1, np.inf, dtype=np.float64)
        used = np.zeros(column_count + 1, dtype=bool)
        column0 = 0
        while True:
            used[column0] = True
            current_row = int(p[column0])
            delta = np.inf
            column1 = 0
            for column in range(1, column_count + 1):
                if used[column]:
                    continue
                reduced = costs[current_row - 1, column - 1] - u[current_row] - v[column]
                if reduced < min_values[column]:
                    min_values[column] = reduced
                    way[column] = column0
                if min_values[column] < delta:
                    delta = min_values[column]
                    column1 = column
            if not np.isfinite(delta):
                break
            for column in range(column_count + 1):
                if used[column]:
                    u[p[column]] += delta
                    v[column] -= delta
                else:
                    min_values[column] -= delta
            column0 = column1
            if p[column0] == 0:
                break
        while column0 != 0:
            previous = int(way[column0])
            p[column0] = p[previous]
            column0 = previous
    assignment = [-1] * row_count
    for column in range(1, column_count + 1):
        if p[column] > 0:
            assignment[int(p[column]) - 1] = column - 1
    return assignment


def _legacy_shape_cost_between_bundle_and_slot(
    bundle_features: dict[str, Any], slot_features: dict[str, Any]
) -> float:
    """Exact v9 descriptor cost, retained for rollback compatibility."""
    bundle_contour = bundle_features.get("contour")
    slot_contour = slot_features.get("contour")
    if bundle_contour is None or slot_contour is None:
        return float("inf")
    shape_distance = float(
        cv2.matchShapes(
            bundle_contour,
            slot_contour,
            cv2.CONTOURS_MATCH_I1,
            0.0,
        )
    )
    if not np.isfinite(shape_distance):
        return float("inf")
    area_ratio = max(bundle_features["area"], slot_features["area"]) / max(
        1.0, min(bundle_features["area"], slot_features["area"])
    )
    aspect_ratio = max(bundle_features["aspect"], slot_features["aspect"]) / max(
        1e-6, min(bundle_features["aspect"], slot_features["aspect"])
    )
    return float(
        0.70 * min(shape_distance, 1.5)
        + 0.20 * abs(math.log(max(area_ratio, 1e-6)))
        + 0.10 * abs(math.log(max(aspect_ratio, 1e-6)))
    )


def _match_bundles_to_color_slots_v9(
    fragments: list[Fragment],
    bundles: list[dict[str, Any]],
    reference_records: list[dict[str, Any]],
    target: np.ndarray,
    config: AssemblyConfig,
) -> tuple[
    list[np.ndarray | None],
    dict[int, dict[str, Any]],
    list[int],
    np.ndarray,
    set[int],
    dict[str, Any],
]:
    """Classify color/X-ray masks and conservatively match them 1:1.

    The historical v9 path accepted every geometrically valid positive-score
    candidate because dummy/unassigned columns had zero cost.  The optional
    conservative layer keeps the same fast candidate generation but requires a
    clear component-side and slot-side winner before committing a placement.
    """

    def _runner_up_margin(
        ranked: list[dict[str, Any]], candidate: dict[str, Any]
    ) -> float | None:
        if not ranked or ranked[0] is not candidate:
            return None if not ranked else float(candidate["combinedScore"] - ranked[0]["combinedScore"])
        if len(ranked) == 1:
            return None
        return float(ranked[0]["combinedScore"] - ranked[1]["combinedScore"])

    def _margin_passes(value: float | None, threshold: float) -> bool:
        return value is None or float(value) >= float(threshold)

    def _serializable_margin(value: float | None) -> float | None:
        return None if value is None else float(value)

    final_poses: list[np.ndarray | None] = [None] * len(fragments)
    component_reports: dict[int, dict[str, Any]] = {}
    unassigned: list[int] = []
    occupied = np.zeros_like(target)
    claimed_slots: set[int] = set()
    reference_features = [
        _reference_slot_features(index, record)
        for index, record in enumerate(reference_records)
    ]
    bundle_features = [_bundle_shape_features(bundle) for bundle in bundles]
    top_k = max(1, int(config.fragment_array_color_slot_top_k))
    maximum_shape_cost = float(config.fragment_array_color_slot_max_shape_cost)
    minimum_combined_score = float(config.fragment_array_color_slot_min_score)
    conservative_enabled = bool(
        config.fragment_array_color_slot_conservative_assignment_enabled
    )
    unassigned_score = float(config.fragment_array_color_slot_unassigned_score)
    require_mutual_best = bool(config.fragment_array_color_slot_require_mutual_best)
    minimum_bundle_margin = float(config.fragment_array_color_slot_min_bundle_margin)
    minimum_slot_margin = float(config.fragment_array_color_slot_min_slot_margin)
    minimum_local_candidate_margin = float(
        config.fragment_array_min_candidate_score_margin
    )
    minimum_local_response_margin = float(config.fragment_array_min_response_margin)
    empty_occupied = np.zeros_like(target)
    candidates_by_pair: dict[tuple[int, int], dict[str, Any]] = {}
    candidate_generation_stats: dict[int, dict[str, int]] = {
        index: {
            "shapePrefilterCandidateCount": 0,
            "alignmentCandidateCount": 0,
            "geometricallyValidCandidateCount": 0,
            "assignmentEligibleCandidateCount": 0,
        }
        for index in range(len(bundles))
    }

    for bundle_index, bundle in enumerate(bundles):
        capture_metadata = _component_capture_metadata(fragments, bundle["indices"])
        if capture_metadata["componentRole"] == "residual_noise_component":
            continue
        ranked_slots: list[tuple[float, int]] = []
        for slot_index, slot_features in enumerate(reference_features):
            shape_cost = _legacy_shape_cost_between_bundle_and_slot(
                bundle_features[bundle_index], slot_features
            )
            if np.isfinite(shape_cost) and shape_cost <= maximum_shape_cost:
                ranked_slots.append((float(shape_cost), int(slot_index)))
        ranked_slots.sort(key=lambda item: (item[0], item[1]))
        candidate_generation_stats[bundle_index]["shapePrefilterCandidateCount"] = len(
            ranked_slots
        )
        for shape_cost, slot_index in ranked_slots[:top_k]:
            alignment = _align_bundle_to_reference_slot(
                bundle["mask"],
                reference_features[slot_index],
                empty_occupied,
                config,
            )
            if alignment is None:
                continue
            candidate_generation_stats[bundle_index]["alignmentCandidateCount"] += 1
            combined_score = float(alignment["score"] - 0.22 * shape_cost)
            base_rejection_reasons: list[str] = []
            if combined_score < minimum_combined_score:
                base_rejection_reasons.append("combined_score_below_minimum")
            if float(alignment["insideRatio"]) < float(
                config.fragment_array_min_inside_ratio
            ):
                base_rejection_reasons.append("inside_ratio_below_minimum")
            if float(alignment["slotCoverage"]) < float(
                config.fragment_array_min_slot_coverage
            ):
                base_rejection_reasons.append("slot_coverage_below_minimum")
            if (
                bool(config.fragment_array_color_slot_voronoi_search_enabled)
                and float(alignment.get("searchRegionInsideRatio", 1.0))
                < float(config.fragment_array_color_slot_voronoi_min_inside_ratio)
            ):
                base_rejection_reasons.append("outside_voronoi_search_region")
            valid = not base_rejection_reasons
            if valid:
                candidate_generation_stats[bundle_index][
                    "geometricallyValidCandidateCount"
                ] += 1
            candidate = {
                "xrayComponentIndex": int(bundle_index),
                "referenceSlotIndex": int(slot_index),
                "shapeCost": float(shape_cost),
                "combinedScore": float(combined_score),
                "valid": bool(valid),
                "baseRejectionReasons": base_rejection_reasons,
                "alignment": alignment,
            }
            candidates_by_pair[(bundle_index, slot_index)] = candidate

    valid_by_bundle: dict[int, list[dict[str, Any]]] = {
        index: [] for index in range(len(bundles))
    }
    valid_by_slot: dict[int, list[dict[str, Any]]] = {
        index: [] for index in range(len(reference_records))
    }
    for candidate in candidates_by_pair.values():
        if not bool(candidate["valid"]):
            continue
        valid_by_bundle[int(candidate["xrayComponentIndex"])].append(candidate)
        valid_by_slot[int(candidate["referenceSlotIndex"])].append(candidate)
    for ranked in valid_by_bundle.values():
        ranked.sort(
            key=lambda item: (-float(item["combinedScore"]), int(item["referenceSlotIndex"]))
        )
    for ranked in valid_by_slot.values():
        ranked.sort(
            key=lambda item: (-float(item["combinedScore"]), int(item["xrayComponentIndex"]))
        )

    candidate_summaries: dict[int, list[dict[str, Any]]] = {
        index: [] for index in range(len(bundles))
    }
    for (bundle_index, slot_index), candidate in candidates_by_pair.items():
        bundle_ranked = valid_by_bundle[bundle_index]
        slot_ranked = valid_by_slot[slot_index]
        bundle_best = bool(bundle_ranked and bundle_ranked[0] is candidate)
        slot_best = bool(slot_ranked and slot_ranked[0] is candidate)
        bundle_margin = _runner_up_margin(bundle_ranked, candidate)
        slot_margin = _runner_up_margin(slot_ranked, candidate)
        mutual_best = bool(bundle_best and slot_best)
        alignment = candidate["alignment"]
        eligibility_reasons = list(candidate["baseRejectionReasons"])
        if bool(candidate["valid"]) and conservative_enabled:
            if float(candidate["combinedScore"]) < max(
                minimum_combined_score, unassigned_score
            ):
                eligibility_reasons.append("does_not_beat_unassigned_score")
            if not _margin_passes(bundle_margin, minimum_bundle_margin):
                eligibility_reasons.append("ambiguous_xray_component_candidates")
            if not _margin_passes(slot_margin, minimum_slot_margin):
                eligibility_reasons.append("ambiguous_color_slot_competition")
            if require_mutual_best and not mutual_best:
                eligibility_reasons.append("not_mutual_best")
            # Local translation/rotation response margins are retained as review
            # diagnostics only.  In real X-ray arrays their numerical scale is
            # commonly 1e-3 to 1e-2, so treating the legacy 0.02 review threshold
            # as a hard rejection criterion suppresses every otherwise clear
            # cross-slot winner.
        assignment_eligible = bool(
            candidate["valid"] and (not conservative_enabled or not eligibility_reasons)
        )
        candidate["bundleBest"] = bundle_best
        candidate["slotBest"] = slot_best
        candidate["mutualBest"] = mutual_best
        candidate["bundleScoreMargin"] = bundle_margin
        candidate["slotScoreMargin"] = slot_margin
        candidate["assignmentEligible"] = assignment_eligible
        candidate["assignmentRejectionReasons"] = eligibility_reasons
        if assignment_eligible:
            candidate_generation_stats[bundle_index][
                "assignmentEligibleCandidateCount"
            ] += 1
        candidate_summaries[bundle_index].append(
            {
                "referenceSlotIndex": int(slot_index),
                "shapeCost": float(candidate["shapeCost"]),
                "combinedScore": float(candidate["combinedScore"]),
                "insideRatio": float(alignment["insideRatio"]),
                "slotCoverage": float(alignment["slotCoverage"]),
                "boundaryPrecision": float(alignment["boundaryPrecision"]),
                "rotationDeg": float(alignment["rotationDeg"]),
                "candidateScoreMargin": float(alignment["candidateScoreMargin"]),
                "responseMargin": float(alignment["responseMargin"]),
                "bundleBest": bundle_best,
                "slotBest": slot_best,
                "mutualBest": mutual_best,
                "bundleScoreMargin": _serializable_margin(bundle_margin),
                "slotScoreMargin": _serializable_margin(slot_margin),
                "valid": bool(candidate["valid"]),
                "assignmentEligible": assignment_eligible,
                "rejectionReasons": list(eligibility_reasons),
            }
        )
    for summaries in candidate_summaries.values():
        summaries.sort(
            key=lambda item: (-float(item["combinedScore"]), int(item["referenceSlotIndex"]))
        )

    bundle_count = len(bundles)
    slot_count = len(reference_records)
    invalid_cost = 1.0e6
    cost_matrix = np.zeros((bundle_count, slot_count + bundle_count), dtype=np.float64)
    if slot_count > 0:
        cost_matrix[:, :slot_count] = invalid_cost
    for (bundle_index, slot_index), candidate in candidates_by_pair.items():
        if bool(candidate["assignmentEligible"]):
            cost_matrix[bundle_index, slot_index] = -float(candidate["combinedScore"])
    assigned_columns = _minimum_cost_row_assignment(cost_matrix)

    selected: dict[int, dict[str, Any]] = {}
    for bundle_index, column_index in enumerate(assigned_columns):
        if column_index < 0 or column_index >= slot_count:
            continue
        candidate = candidates_by_pair.get((bundle_index, column_index))
        if candidate is None or not bool(candidate["assignmentEligible"]):
            continue
        selected[bundle_index] = candidate

    post_assignment_rejections: dict[int, str] = {}
    # Commit strongest matched placements first and reject any unexpected
    # cross-slot collision after the global 1:1 assignment.
    for bundle_index in sorted(
        selected,
        key=lambda index: float(selected[index]["combinedScore"]),
        reverse=True,
    ):
        bundle = bundles[bundle_index]
        candidate = selected[bundle_index]
        slot_index = int(candidate["referenceSlotIndex"])
        alignment = candidate["alignment"]
        placed_mask = alignment["placedMask"]
        placed_binary = placed_mask > 0
        actual_overlap_ratio = float(
            np.count_nonzero(placed_binary & (occupied > 0))
        ) / max(int(np.count_nonzero(placed_binary)), 1)
        if actual_overlap_ratio > float(config.fragment_array_max_occupied_overlap_ratio):
            post_assignment_rejections[bundle_index] = (
                "occupied_overlap_after_global_assignment"
            )
            continue
        transform = alignment["transform"]
        for fragment_index in bundle["indices"]:
            final_poses[fragment_index] = transform @ bundle["poses"][fragment_index]
        occupied[placed_binary] = 255
        slot_record = reference_records[slot_index]
        slot_x, slot_y, slot_width, slot_height = slot_record["bbox"]
        occupied[
            slot_y : slot_y + slot_height,
            slot_x : slot_x + slot_width,
        ][slot_record["mask"] > 0] = 255
        claimed_slots.add(slot_index)
        capture_metadata = _component_capture_metadata(fragments, bundle["indices"])
        review_required = bool(
            float(alignment["insideRatio"])
            < float(config.fragment_array_review_inside_ratio)
            or float(alignment["candidateScoreMargin"])
            < minimum_local_candidate_margin
            or float(alignment["responseMargin"])
            < minimum_local_response_margin
            or float(candidate["combinedScore"])
            < max(minimum_combined_score + 0.08, 0.18)
            or (
                not conservative_enabled
                and (
                    not bool(candidate["mutualBest"])
                    or not _margin_passes(
                        candidate["bundleScoreMargin"], minimum_bundle_margin
                    )
                    or not _margin_passes(
                        candidate["slotScoreMargin"], minimum_slot_margin
                    )
                )
            )
        )
        component_reports[bundle_index] = {
            "xrayComponentIndex": int(bundle_index),
            "fragmentIndices": bundle["indices"],
            **capture_metadata,
            "status": "assigned_to_fragment_array",
            "matchingMode": (
                "color_and_xray_component_shape_matching_conservative"
                if conservative_enabled
                else "color_and_xray_component_shape_matching"
            ),
            "primaryReferenceSlotIndex": int(slot_index),
            "referenceSlotIndices": [int(slot_index)],
            "referenceSlotAssignments": [
                {
                    "referenceSlotIndex": int(slot_index),
                    "intersectionPx": int(alignment["intersectionPx"]),
                    "slotCoverage": float(alignment["slotCoverage"]),
                }
            ],
            "shapeCost": float(candidate["shapeCost"]),
            "combinedMatchScore": float(candidate["combinedScore"]),
            "mutualBest": bool(candidate["mutualBest"]),
            "bundleScoreMargin": _serializable_margin(
                candidate["bundleScoreMargin"]
            ),
            "slotScoreMargin": _serializable_margin(candidate["slotScoreMargin"]),
            "alignment": {
                key: value
                for key, value in alignment.items()
                if key not in {"transform", "placedMask"}
            },
            "candidateReferenceSlotAssignments": candidate_summaries[bundle_index],
            "reviewRequired": review_required,
        }

    unassigned_reason_counts: dict[str, int] = {}
    for bundle_index, bundle in enumerate(bundles):
        if bundle_index in component_reports:
            continue
        unassigned.append(bundle_index)
        capture_metadata = _component_capture_metadata(fragments, bundle["indices"])
        summaries = candidate_summaries[bundle_index]
        valid_candidates = [item for item in summaries if bool(item["valid"])]
        eligible_candidates = [
            item for item in summaries if bool(item["assignmentEligible"])
        ]
        if capture_metadata["componentRole"] == "residual_noise_component":
            unassigned_reason = "residual_noise_component"
            reason_details: list[str] = []
        elif bundle_index in post_assignment_rejections:
            unassigned_reason = post_assignment_rejections[bundle_index]
            reason_details = [unassigned_reason]
        elif not summaries:
            stats = candidate_generation_stats[bundle_index]
            if stats["shapePrefilterCandidateCount"] == 0:
                unassigned_reason = "no_shape_candidate"
            else:
                unassigned_reason = "no_alignment_candidate"
            reason_details = []
        elif not valid_candidates:
            unassigned_reason = "all_candidates_failed_geometric_validation"
            reason_details = sorted(
                {
                    reason
                    for item in summaries
                    for reason in item.get("rejectionReasons", [])
                }
            )
        elif not eligible_candidates:
            unassigned_reason = "low_confidence_or_ambiguous_candidates"
            reason_details = list(summaries[0].get("rejectionReasons", []))
        else:
            unassigned_reason = "global_one_to_one_conflict"
            reason_details = []
        unassigned_reason_counts[unassigned_reason] = (
            unassigned_reason_counts.get(unassigned_reason, 0) + 1
        )
        component_reports[bundle_index] = {
            "xrayComponentIndex": int(bundle_index),
            "fragmentIndices": bundle["indices"],
            **capture_metadata,
            "status": "unassigned_parked_for_hitl",
            "matchingMode": (
                "color_and_xray_component_shape_matching_conservative"
                if conservative_enabled
                else "color_and_xray_component_shape_matching"
            ),
            "unassignedReason": unassigned_reason,
            "unassignedReasonDetails": reason_details,
            "candidateGeneration": candidate_generation_stats[bundle_index],
            "referenceSlotAssignments": [],
            "candidateReferenceSlotAssignments": summaries,
            "reviewRequired": capture_metadata["componentRole"] != "residual_noise_component",
        }

    matching_debug = {
        "mode": (
            "color_and_xray_component_shape_matching_conservative"
            if conservative_enabled
            else "color_and_xray_component_shape_matching"
        ),
        "referenceSlotCount": int(slot_count),
        "xrayComponentCount": int(bundle_count),
        "validCandidatePairCount": int(
            sum(bool(candidate["valid"]) for candidate in candidates_by_pair.values())
        ),
        "assignmentEligibleCandidatePairCount": int(
            sum(
                bool(candidate["assignmentEligible"])
                for candidate in candidates_by_pair.values()
            )
        ),
        "selectedPairCount": int(len(claimed_slots)),
        "postAssignmentCollisionRejectionCount": int(
            len(post_assignment_rejections)
        ),
        "topKPerXrayComponent": int(top_k),
        "maximumShapeCost": float(maximum_shape_cost),
        "minimumCombinedScore": float(minimum_combined_score),
        "conservativeAssignmentEnabled": conservative_enabled,
        "explicitUnassignedScore": float(unassigned_score),
        "requireMutualBest": bool(require_mutual_best),
        "minimumBundleScoreMargin": float(minimum_bundle_margin),
        "minimumSlotScoreMargin": float(minimum_slot_margin),
        "minimumLocalCandidateScoreMargin": float(minimum_local_candidate_margin),
        "minimumLocalResponseMargin": float(minimum_local_response_margin),
        "unassignedReasonCounts": unassigned_reason_counts,
    }
    return (
        final_poses,
        component_reports,
        unassigned,
        occupied,
        claimed_slots,
        matching_debug,
    )
def _match_bundles_to_color_slots_v10(
    fragments: list[Fragment],
    bundles: list[dict[str, Any]],
    reference_records: list[dict[str, Any]],
    target: np.ndarray,
    config: AssemblyConfig,
) -> tuple[
    list[np.ndarray | None],
    dict[int, dict[str, Any]],
    list[int],
    np.ndarray,
    set[int],
    dict[str, Any],
]:
    """Match X-ray components to individually segmented color slots.

    v10 keeps v9's one-to-one assignment, but makes candidate generation more
    conservative: a cheap invariant descriptor narrows the slot set, a
    canonical contour Chamfer distance reranks it, and optional mutual-best
    margins reject ambiguous assignments before the Hungarian step.
    """
    final_poses: list[np.ndarray | None] = [None] * len(fragments)
    component_reports: dict[int, dict[str, Any]] = {}
    unassigned: list[int] = []
    occupied = np.zeros_like(target)
    claimed_slots: set[int] = set()
    reference_features = [
        _reference_slot_features(index, record)
        for index, record in enumerate(reference_records)
    ]
    bundle_features = [_bundle_shape_features(bundle) for bundle in bundles]
    top_k = max(1, int(config.fragment_array_color_slot_top_k))
    preselect_count = max(top_k, top_k * 3)
    maximum_shape_cost = float(config.fragment_array_color_slot_max_shape_cost)
    minimum_combined_score = float(config.fragment_array_color_slot_min_score)
    chamfer_weight = float(config.fragment_array_color_slot_chamfer_weight)
    mutual_best_enabled = bool(config.fragment_array_color_slot_mutual_best_enabled)
    minimum_bundle_margin = float(config.fragment_array_color_slot_min_bundle_margin)
    minimum_slot_margin = float(config.fragment_array_color_slot_min_slot_margin)
    empty_occupied = np.zeros_like(target)
    candidates_by_pair: dict[tuple[int, int], dict[str, Any]] = {}
    candidate_summaries: dict[int, list[dict[str, Any]]] = {
        index: [] for index in range(len(bundles))
    }

    for bundle_index, bundle in enumerate(bundles):
        capture_metadata = _component_capture_metadata(fragments, bundle["indices"])
        if capture_metadata["componentRole"] == "residual_noise_component":
            continue
        preliminary: list[tuple[float, int]] = []
        for slot_index, slot_features in enumerate(reference_features):
            shape_cost, _ = _shape_cost_between_bundle_and_slot(
                bundle_features[bundle_index],
                slot_features,
                include_area=True,
                chamfer_weight=0.0,
            )
            if np.isfinite(shape_cost):
                preliminary.append((float(shape_cost), int(slot_index)))
        preliminary.sort(key=lambda item: (item[0], item[1]))

        ranked_slots: list[tuple[float, int, dict[str, float]]] = []
        for _, slot_index in preliminary[:preselect_count]:
            shape_cost, shape_metrics = _shape_cost_between_bundle_and_slot(
                bundle_features[bundle_index],
                reference_features[slot_index],
                include_area=True,
                chamfer_weight=chamfer_weight,
            )
            if np.isfinite(shape_cost) and shape_cost <= maximum_shape_cost:
                ranked_slots.append((float(shape_cost), int(slot_index), shape_metrics))
        ranked_slots.sort(key=lambda item: (item[0], item[1]))

        for shape_cost, slot_index, shape_metrics in ranked_slots[:top_k]:
            alignment = _align_bundle_to_reference_slot(
                bundle["mask"],
                reference_features[slot_index],
                empty_occupied,
                config,
            )
            if alignment is None:
                continue
            combined_score = float(alignment["score"] - 0.22 * shape_cost)
            valid = bool(
                combined_score >= minimum_combined_score
                and float(alignment["insideRatio"])
                >= float(config.fragment_array_min_inside_ratio)
                and float(alignment["slotCoverage"])
                >= float(config.fragment_array_min_slot_coverage)
                and (
                    not bool(config.fragment_array_color_slot_voronoi_search_enabled)
                    or float(alignment.get("searchRegionInsideRatio", 1.0))
                    >= float(config.fragment_array_color_slot_voronoi_min_inside_ratio)
                )
            )
            candidate = {
                "xrayComponentIndex": int(bundle_index),
                "referenceSlotIndex": int(slot_index),
                "shapeCost": float(shape_cost),
                "shapeMetrics": shape_metrics,
                "combinedScore": float(combined_score),
                "valid": valid,
                "alignment": alignment,
                "mutualBest": False,
                "bundleScoreMargin": 0.0,
                "slotScoreMargin": 0.0,
                "assignmentEligible": False,
            }
            candidates_by_pair[(bundle_index, slot_index)] = candidate

    # Compute directional best choices and ambiguity margins before global 1:1
    # assignment.  This prevents a merely relative best match from being forced
    # into a color slot when both directions do not agree.
    valid_by_bundle: dict[int, list[dict[str, Any]]] = {
        index: [] for index in range(len(bundles))
    }
    valid_by_slot: dict[int, list[dict[str, Any]]] = {
        index: [] for index in range(len(reference_records))
    }
    for candidate in candidates_by_pair.values():
        if not bool(candidate["valid"]):
            continue
        valid_by_bundle[int(candidate["xrayComponentIndex"])].append(candidate)
        valid_by_slot[int(candidate["referenceSlotIndex"])].append(candidate)
    for candidates in valid_by_bundle.values():
        candidates.sort(
            key=lambda item: (
                -float(item["combinedScore"]),
                float(item["shapeCost"]),
                int(item["referenceSlotIndex"]),
            )
        )
    for candidates in valid_by_slot.values():
        candidates.sort(
            key=lambda item: (
                -float(item["combinedScore"]),
                float(item["shapeCost"]),
                int(item["xrayComponentIndex"]),
            )
        )

    for (bundle_index, slot_index), candidate in candidates_by_pair.items():
        bundle_ranked = valid_by_bundle.get(bundle_index, [])
        slot_ranked = valid_by_slot.get(slot_index, [])
        bundle_best = bundle_ranked[0] if bundle_ranked else None
        slot_best = slot_ranked[0] if slot_ranked else None
        bundle_margin = (
            float(bundle_ranked[0]["combinedScore"] - bundle_ranked[1]["combinedScore"])
            if len(bundle_ranked) > 1
            else 1.0
        )
        slot_margin = (
            float(slot_ranked[0]["combinedScore"] - slot_ranked[1]["combinedScore"])
            if len(slot_ranked) > 1
            else 1.0
        )
        mutual = bool(
            bundle_best is candidate
            and slot_best is candidate
        )
        eligible = bool(candidate["valid"])
        if mutual_best_enabled:
            eligible = bool(
                eligible
                and mutual
                and bundle_margin >= minimum_bundle_margin
                and slot_margin >= minimum_slot_margin
            )
        candidate["mutualBest"] = mutual
        candidate["bundleScoreMargin"] = float(bundle_margin)
        candidate["slotScoreMargin"] = float(slot_margin)
        candidate["assignmentEligible"] = eligible

    for bundle_index in range(len(bundles)):
        summaries: list[dict[str, Any]] = []
        for (candidate_bundle, _), candidate in candidates_by_pair.items():
            if candidate_bundle != bundle_index:
                continue
            alignment = candidate["alignment"]
            metrics = candidate.get("shapeMetrics", {})
            summaries.append(
                {
                    "referenceSlotIndex": int(candidate["referenceSlotIndex"]),
                    "shapeCost": float(candidate["shapeCost"]),
                    "huDistance": metrics.get("huDistance"),
                    "chamferDistance": metrics.get("chamferDistance"),
                    "combinedScore": float(candidate["combinedScore"]),
                    "insideRatio": float(alignment["insideRatio"]),
                    "slotCoverage": float(alignment["slotCoverage"]),
                    "boundaryPrecision": float(alignment["boundaryPrecision"]),
                    "rotationDeg": float(alignment["rotationDeg"]),
                    "valid": bool(candidate["valid"]),
                    "mutualBest": bool(candidate["mutualBest"]),
                    "bundleScoreMargin": float(candidate["bundleScoreMargin"]),
                    "slotScoreMargin": float(candidate["slotScoreMargin"]),
                    "assignmentEligible": bool(candidate["assignmentEligible"]),
                }
            )
        summaries.sort(key=lambda item: (-item["combinedScore"], item["referenceSlotIndex"]))
        candidate_summaries[bundle_index] = summaries

    bundle_count = len(bundles)
    slot_count = len(reference_records)
    invalid_cost = 1.0e6
    cost_matrix = np.zeros((bundle_count, slot_count + bundle_count), dtype=np.float64)
    if slot_count > 0:
        cost_matrix[:, :slot_count] = invalid_cost
    for (bundle_index, slot_index), candidate in candidates_by_pair.items():
        if bool(candidate["assignmentEligible"]):
            cost_matrix[bundle_index, slot_index] = -float(candidate["combinedScore"])
    assigned_columns = _minimum_cost_row_assignment(cost_matrix)

    selected: dict[int, dict[str, Any]] = {}
    for bundle_index, column_index in enumerate(assigned_columns):
        if column_index < 0 or column_index >= slot_count:
            continue
        candidate = candidates_by_pair.get((bundle_index, column_index))
        if candidate is None or not bool(candidate["assignmentEligible"]):
            continue
        selected[bundle_index] = candidate

    refine_dimension = int(config.fragment_array_color_slot_refine_max_dimension)
    refine_config = (
        replace(config, fragment_array_alignment_max_dimension=refine_dimension)
        if refine_dimension > int(config.fragment_array_alignment_max_dimension)
        else config
    )

    # Commit strongest matched placements first and reject any unexpected
    # cross-slot collision after the global 1:1 assignment.
    for bundle_index in sorted(
        selected,
        key=lambda index: float(selected[index]["combinedScore"]),
        reverse=True,
    ):
        bundle = bundles[bundle_index]
        candidate = selected[bundle_index]
        slot_index = int(candidate["referenceSlotIndex"])
        alignment = candidate["alignment"]
        if refine_config is not config:
            refined = _align_bundle_to_reference_slot(
                bundle["mask"],
                reference_features[slot_index],
                np.zeros_like(target),
                refine_config,
            )
            if refined is not None and float(refined["score"]) >= float(alignment["score"]) - 0.03:
                alignment = refined
        placed_mask = alignment["placedMask"]
        placed_binary = placed_mask > 0
        actual_overlap_ratio = float(
            np.count_nonzero(placed_binary & (occupied > 0))
        ) / max(int(np.count_nonzero(placed_binary)), 1)
        if actual_overlap_ratio > float(config.fragment_array_max_occupied_overlap_ratio):
            continue
        transform = alignment["transform"]
        for fragment_index in bundle["indices"]:
            final_poses[fragment_index] = transform @ bundle["poses"][fragment_index]
        occupied[placed_binary] = 255
        slot_record = reference_records[slot_index]
        slot_x, slot_y, slot_width, slot_height = slot_record["bbox"]
        occupied[
            slot_y : slot_y + slot_height,
            slot_x : slot_x + slot_width,
        ][slot_record["mask"] > 0] = 255
        claimed_slots.add(slot_index)
        capture_metadata = _component_capture_metadata(fragments, bundle["indices"])
        review_required = bool(
            float(alignment["insideRatio"])
            < float(config.fragment_array_review_inside_ratio)
            or float(alignment["candidateScoreMargin"])
            < float(config.fragment_array_min_candidate_score_margin)
            or float(alignment["responseMargin"])
            < float(config.fragment_array_min_response_margin)
            or not bool(candidate["mutualBest"])
        )
        component_reports[bundle_index] = {
            "xrayComponentIndex": int(bundle_index),
            "fragmentIndices": bundle["indices"],
            **capture_metadata,
            "status": "assigned_to_fragment_array",
            "matchingMode": "robust_color_xray_mutual_shape_matching",
            "primaryReferenceSlotIndex": int(slot_index),
            "referenceSlotIndices": [int(slot_index)],
            "referenceSlotAssignments": [
                {
                    "referenceSlotIndex": int(slot_index),
                    "intersectionPx": int(alignment["intersectionPx"]),
                    "slotCoverage": float(alignment["slotCoverage"]),
                }
            ],
            "shapeCost": float(candidate["shapeCost"]),
            "shapeMetrics": candidate.get("shapeMetrics", {}),
            "combinedMatchScore": float(candidate["combinedScore"]),
            "mutualBest": bool(candidate["mutualBest"]),
            "bundleScoreMargin": float(candidate["bundleScoreMargin"]),
            "slotScoreMargin": float(candidate["slotScoreMargin"]),
            "alignment": {
                key: value
                for key, value in alignment.items()
                if key not in {"transform", "placedMask"}
            },
            "candidateReferenceSlotAssignments": candidate_summaries[bundle_index],
            "reviewRequired": review_required,
        }

    for bundle_index, bundle in enumerate(bundles):
        if bundle_index in component_reports:
            continue
        unassigned.append(bundle_index)
        capture_metadata = _component_capture_metadata(fragments, bundle["indices"])
        component_reports[bundle_index] = {
            "xrayComponentIndex": int(bundle_index),
            "fragmentIndices": bundle["indices"],
            **capture_metadata,
            "status": "unassigned_parked_for_hitl",
            "matchingMode": "robust_color_xray_mutual_shape_matching",
            "referenceSlotAssignments": [],
            "candidateReferenceSlotAssignments": candidate_summaries[bundle_index],
            "reviewRequired": capture_metadata["componentRole"] != "residual_noise_component",
        }

    matching_debug = {
        "mode": "robust_color_xray_mutual_shape_matching",
        "referenceSlotCount": int(slot_count),
        "xrayComponentCount": int(bundle_count),
        "validCandidatePairCount": int(
            sum(bool(candidate["valid"]) for candidate in candidates_by_pair.values())
        ),
        "eligibleCandidatePairCount": int(
            sum(bool(candidate["assignmentEligible"]) for candidate in candidates_by_pair.values())
        ),
        "mutualBestCandidatePairCount": int(
            sum(bool(candidate["mutualBest"]) for candidate in candidates_by_pair.values())
        ),
        "selectedPairCount": int(len(claimed_slots)),
        "topKPerXrayComponent": int(top_k),
        "preselectCountPerXrayComponent": int(preselect_count),
        "maximumShapeCost": float(maximum_shape_cost),
        "minimumCombinedScore": float(minimum_combined_score),
        "chamferWeight": float(chamfer_weight),
        "mutualBestEnabled": bool(mutual_best_enabled),
        "minimumBundleScoreMargin": float(minimum_bundle_margin),
        "minimumSlotScoreMargin": float(minimum_slot_margin),
        "refineMaxDimension": int(refine_dimension),
        "referenceSearchRegionMode": (
            "nearest_fragment_voronoi_clipped_by_expansion"
            if bool(config.fragment_array_color_slot_voronoi_search_enabled)
            else "rectangular_padding"
        ),
        "voronoiMinimumInsideRatio": float(
            config.fragment_array_color_slot_voronoi_min_inside_ratio
        ),
        "voronoiSlotCount": int(
            sum(record.get("searchMask") is not None for record in reference_records)
        ),
        "voronoiBoundaryTouchingFragmentCount": int(
            sum(
                bool(record.get("fragmentTouchesSearchBoundary", False))
                for record in reference_records
            )
        ),
    }
    return (
        final_poses,
        component_reports,
        unassigned,
        occupied,
        claimed_slots,
        matching_debug,
    )


def _match_bundles_to_color_slots(
    fragments: list[Fragment],
    bundles: list[dict[str, Any]],
    reference_records: list[dict[str, Any]],
    target: np.ndarray,
    config: AssemblyConfig,
) -> tuple[
    list[np.ndarray | None],
    dict[int, dict[str, Any]],
    list[int],
    np.ndarray,
    set[int],
    dict[str, Any],
]:
    if bool(config.fragment_array_color_slot_accuracy_v10_enabled):
        return _match_bundles_to_color_slots_v10(
            fragments, bundles, reference_records, target, config
        )
    return _match_bundles_to_color_slots_v9(
        fragments, bundles, reference_records, target, config
    )


def _component_capture_metadata(
    fragments: list[Fragment], component_indices: list[int]
) -> dict[str, Any]:
    roles = [_fragment_capture_role(fragments[index]) for index in component_indices]
    source_count = len(
        {
            str(fragments[index].source_path or fragments[index].path)
            for index in component_indices
        }
    )
    has_partial = any(role == "partial_capture_candidate" for role in roles)
    if has_partial and source_count > 1:
        component_role = "stitched_partial_capture_component"
    elif has_partial:
        component_role = "partial_capture_candidate_unconsolidated"
    elif any(role == "residual_noise_group" for role in roles):
        component_role = "residual_noise_component"
    else:
        component_role = "independent_fragment_component"
    return {
        "captureRoles": roles,
        "componentRole": component_role,
        "sourceImageCount": int(source_count),
    }



def _source_frame_key(fragment: Fragment) -> str:
    return str(fragment.source_path or fragment.path)


def _reference_anchored_source_bundles(
    fragments: list[Fragment],
) -> list[dict[str, Any]]:
    """Reconstruct each original X-ray frame as one rigid placement unit.

    Multi-object segmentation creates cropped fragment objects.  Their
    ``crop_bbox_xywh`` values are absolute coordinates in the unmodified source
    frame, so using those translations preserves the source-frame constellation
    without stitching pixels from different acquisitions.
    """
    grouped: dict[str, list[int]] = {}
    for fragment in fragments:
        grouped.setdefault(_source_frame_key(fragment), []).append(int(fragment.index))

    source_poses = [np.eye(3, dtype=np.float64) for _ in fragments]
    records: list[dict[str, Any]] = []
    for source_key, indices in grouped.items():
        for index in indices:
            x, y, _, _ = [int(value) for value in fragments[index].crop_bbox_xywh]
            source_poses[index] = _translation(float(x), float(y))
        bundle = _component_local_bundle(fragments, source_poses, indices)
        physical_indices = [
            index
            for index in indices
            if _fragment_capture_role(fragments[index]) != "residual_noise_group"
        ]
        partial_indices = [
            index
            for index in physical_indices
            if _is_partial_capture_candidate(fragments[index])
        ]
        source_indices = [
            _fragment_source_index(fragments[index]) for index in indices
        ]
        records.append(
            {
                **bundle,
                "sourceKey": source_key,
                "sourceName": str(
                    fragments[indices[0]].source_name
                    or (fragments[indices[0]].source_path or fragments[indices[0]].path).name
                ),
                "sourceIndex": int(min(source_indices)) if source_indices else -1,
                "physicalIndices": physical_indices,
                "partialIndices": partial_indices,
                "residualIndices": [
                    index for index in indices if index not in set(physical_indices)
                ],
            }
        )
    records.sort(key=lambda item: (int(item["sourceIndex"]), str(item["sourceKey"])))
    return records


def _placed_fragment_slot_support(
    fragment: Fragment,
    pose: np.ndarray,
    reference_records: list[dict[str, Any]],
    canvas_size: tuple[int, int],
    config: AssemblyConfig,
) -> dict[str, Any]:
    canvas_w, canvas_h = [int(value) for value in canvas_size]
    height, width = fragment.mask.shape
    corners = _transform_points(
        pose,
        np.array(
            [[0.0, 0.0], [width, 0.0], [width, height], [0.0, height]],
            dtype=np.float64,
        ),
    )
    x0 = max(0, int(math.floor(float(corners[:, 0].min()))))
    y0 = max(0, int(math.floor(float(corners[:, 1].min()))))
    x1 = min(canvas_w, int(math.ceil(float(corners[:, 0].max()))))
    y1 = min(canvas_h, int(math.ceil(float(corners[:, 1].max()))))
    if x1 <= x0 or y1 <= y0:
        return {
            "fragmentIndex": int(fragment.index),
            "bestReferenceSlotIndex": None,
            "bestInsideRatio": 0.0,
            "secondInsideRatio": 0.0,
            "slotMargin": 0.0,
            "intersectionPx": 0,
            "placedAreaPx": 0,
            "supported": False,
            "reason": "outside_reference_canvas",
        }
    local_transform = _translation(-float(x0), -float(y0)) @ pose
    placed = cv2.warpAffine(
        (fragment.mask > 0).astype(np.uint8) * 255,
        local_transform[:2].astype(np.float32),
        (x1 - x0, y1 - y0),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    ) > 0
    placed_area = max(int(np.count_nonzero(placed)), 1)
    candidates: list[dict[str, Any]] = []
    for slot_index, record in enumerate(reference_records):
        sx, sy, sw, sh = [int(value) for value in record["bbox"]]
        ox0, oy0 = max(x0, sx), max(y0, sy)
        ox1, oy1 = min(x1, sx + sw), min(y1, sy + sh)
        if ox1 <= ox0 or oy1 <= oy0:
            continue
        placed_crop = placed[oy0 - y0 : oy1 - y0, ox0 - x0 : ox1 - x0]
        slot_crop = (record["mask"] > 0)[
            oy0 - sy : oy1 - sy,
            ox0 - sx : ox1 - sx,
        ]
        intersection = int(np.count_nonzero(placed_crop & slot_crop))
        if intersection < int(config.fragment_array_reference_anchored_min_slot_intersection_px):
            continue
        candidates.append(
            {
                "referenceSlotIndex": int(slot_index),
                "intersectionPx": int(intersection),
                "fragmentInsideRatio": float(intersection / placed_area),
                "slotCoverage": float(intersection / max(int(record["area"]), 1)),
            }
        )
    candidates.sort(
        key=lambda item: (
            float(item["fragmentInsideRatio"]),
            int(item["intersectionPx"]),
        ),
        reverse=True,
    )
    best = candidates[0] if candidates else None
    second_ratio = float(candidates[1]["fragmentInsideRatio"]) if len(candidates) > 1 else 0.0
    best_ratio = float(best["fragmentInsideRatio"]) if best is not None else 0.0
    role = _fragment_capture_role(fragment)
    minimum_inside = (
        float(config.fragment_array_reference_anchored_min_fragment_inside_ratio)
        if role == "partial_capture_candidate"
        else float(config.fragment_array_reference_anchored_min_independent_inside_ratio)
    )
    margin = best_ratio - second_ratio
    supported = bool(
        best is not None
        and best_ratio >= minimum_inside
        and margin >= float(config.fragment_array_reference_anchored_min_fragment_slot_margin)
    )
    return {
        "fragmentIndex": int(fragment.index),
        "captureRole": role,
        "bestReferenceSlotIndex": (
            int(best["referenceSlotIndex"]) if best is not None else None
        ),
        "bestInsideRatio": float(best_ratio),
        "secondInsideRatio": float(second_ratio),
        "slotMargin": float(margin),
        "intersectionPx": int(best["intersectionPx"]) if best is not None else 0,
        "slotCoverage": float(best["slotCoverage"]) if best is not None else 0.0,
        "placedAreaPx": int(placed_area),
        "supported": supported,
        "reason": (
            "supported_by_reference_slot"
            if supported
            else (
                "no_reference_slot_intersection"
                if best is None
                else "low_inside_ratio_or_ambiguous_slot"
            )
        ),
        "candidateReferenceSlots": candidates[:3],
    }


def align_fragment_array_source_frames_to_reference(
    reference_image: np.ndarray,
    reference_mask: np.ndarray,
    fragments: list[Fragment],
    config: AssemblyConfig,
) -> tuple[
    list[np.ndarray],
    SearchGeometry,
    dict[str, Any],
    np.ndarray,
    np.ndarray,
    np.ndarray,
    list[list[int]],
    dict[str, Any],
    np.ndarray | None,
]:
    """Place original X-ray source frames directly in reference coordinates.

    No cross-source image stitch is performed.  Each source frame is a rigid
    group and different source frames may support the same color slot (N:1).
    """
    reference_mask, segmentation_debug = _segment_full_fragment_array_reference(
        reference_image, reference_mask, config
    )
    source_bundles = _reference_anchored_source_bundles(fragments)
    total_xray_area = sum(int(bundle["area"]) for bundle in source_bundles)
    reference_area = int(np.count_nonzero(reference_mask))
    if total_xray_area <= 0 or reference_area <= 0:
        raise ValueError("reference-anchored partial 경로의 입력 면적이 0입니다.")

    if config.reference_scale_override is not None:
        reference_scale = float(config.reference_scale_override)
        scale_debug = {"method": "explicit_override", "selectedScale": reference_scale}
    else:
        reference_scale, scale_debug = _estimate_reference_scale(
            reference_mask, fragments, total_xray_area, config
        )
    reference_h, reference_w = reference_mask.shape
    scaled_w = max(1, int(round(reference_w * reference_scale)))
    scaled_h = max(1, int(round(reference_h * reference_scale)))
    scaled_reference = cv2.resize(
        reference_mask, (scaled_w, scaled_h), interpolation=cv2.INTER_NEAREST
    )
    scaled_preview = cv2.resize(
        normalize_preview(reference_image),
        (scaled_w, scaled_h),
        interpolation=cv2.INTER_AREA if reference_scale < 1.0 else cv2.INTER_LINEAR,
    )
    largest_diagonal = max(
        int(math.ceil(math.hypot(bundle["mask"].shape[1], bundle["mask"].shape[0])))
        for bundle in source_bundles
    )
    margin = max(
        20,
        int(round(max(scaled_w, scaled_h, largest_diagonal) * config.canvas_margin_ratio)),
    )
    base_canvas_w = max(scaled_w, largest_diagonal) + 2 * margin
    base_canvas_h = max(scaled_h, largest_diagonal) + 2 * margin
    target = np.zeros((base_canvas_h, base_canvas_w), dtype=np.uint8)
    preview = np.zeros((base_canvas_h, base_canvas_w, 3), dtype=np.uint8)
    target_x = (base_canvas_w - scaled_w) // 2
    target_y = (base_canvas_h - scaled_h) // 2
    target[target_y : target_y + scaled_h, target_x : target_x + scaled_w] = scaled_reference
    preview[target_y : target_y + scaled_h, target_x : target_x + scaled_w] = scaled_preview
    reference_records = _compact_reference_slot_records(
        target,
        minimum_area_ratio=float(config.fragment_array_reference_min_component_area_ratio),
        config=config,
    )
    reference_slot_search_overlay = (
        _render_reference_slot_search_overlay(preview, reference_records)
        if bool(config.fragment_array_color_slot_voronoi_search_enabled)
        else None
    )

    alignment_config = replace(
        config,
        fragment_array_alignment_max_dimension=int(
            config.fragment_array_reference_anchored_alignment_max_dimension
        ),
        fragment_array_full_rotation_step_deg=int(
            config.fragment_array_reference_anchored_angle_step_deg
        ),
        global_refine_angle_step_deg=float(
            config.fragment_array_reference_anchored_refine_angle_step_deg
        ),
        fragment_array_occupied_overlap_weight=0.0,
    )
    empty_occupied = np.zeros_like(target)
    final_poses: list[np.ndarray | None] = [None] * len(fragments)
    source_reports: list[dict[str, Any]] = []
    accepted_source_indices: list[int] = []
    rejected_source_indices: list[int] = []
    claimed_slots: set[int] = set()

    for source_record_index, bundle in enumerate(source_bundles):
        alignment = _align_bundle_to_array(
            bundle["mask"],
            target,
            empty_occupied,
            alignment_config,
            materialize_placed_mask=False,
        )
        candidate_poses = {
            index: alignment["transform"] @ bundle["poses"][index]
            for index in bundle["indices"]
        }
        supports = [
            _placed_fragment_slot_support(
                fragments[index],
                candidate_poses[index],
                reference_records,
                (base_canvas_w, base_canvas_h),
                config,
            )
            for index in bundle["physicalIndices"]
        ]
        supported = [item for item in supports if bool(item["supported"])]
        physical_count = len(bundle["physicalIndices"])
        if physical_count <= 1:
            required_support = 1
            accepted = bool(
                supported
                and float(alignment["insideRatio"])
                >= float(config.fragment_array_reference_anchored_single_min_inside_ratio)
                and float(alignment["boundaryPrecision"])
                >= float(config.fragment_array_reference_anchored_single_min_boundary_precision)
                and float(alignment["responseMargin"])
                >= float(config.fragment_array_reference_anchored_min_single_response_margin)
            )
        else:
            required_support = max(
                min(
                    int(config.fragment_array_reference_anchored_min_supported_fragments),
                    physical_count,
                ),
                int(
                    math.ceil(
                        physical_count
                        * float(
                            config.fragment_array_reference_anchored_min_supported_fraction
                        )
                    )
                ),
            )
            accepted = bool(
                len(supported) >= required_support
                and float(alignment["insideRatio"])
                >= float(config.fragment_array_reference_anchored_min_source_inside_ratio)
                and float(alignment["boundaryPrecision"])
                >= float(config.fragment_array_reference_anchored_min_boundary_precision)
                and float(alignment["responseMargin"])
                >= float(config.fragment_array_reference_anchored_min_multi_response_margin)
            )
        if accepted:
            accepted_source_indices.append(source_record_index)
            for index, pose in candidate_poses.items():
                final_poses[index] = pose
            for item in supported:
                if item.get("bestReferenceSlotIndex") is not None:
                    claimed_slots.add(int(item["bestReferenceSlotIndex"]))
        else:
            rejected_source_indices.append(source_record_index)
        source_reports.append(
            {
                "sourceFrameIndex": int(source_record_index),
                "sourceIndex": int(bundle["sourceIndex"]),
                "sourceFile": str(bundle["sourceName"]),
                "sourcePath": str(bundle["sourceKey"]),
                "fragmentIndices": [int(index) for index in bundle["indices"]],
                "physicalFragmentCount": int(physical_count),
                "partialCaptureFragmentCount": int(len(bundle["partialIndices"])),
                "residualFragmentCount": int(len(bundle["residualIndices"])),
                "supportedFragmentCount": int(len(supported)),
                "requiredSupportedFragmentCount": int(required_support),
                "status": (
                    "reference_anchored_source_frame_assigned"
                    if accepted
                    else "reference_anchored_source_frame_parked_for_hitl"
                ),
                "alignment": {
                    key: value
                    for key, value in alignment.items()
                    if key not in {"transform", "placedMask"}
                },
                "fragmentSlotSupports": supports,
                "referenceSlotIndices": sorted(
                    {
                        int(item["bestReferenceSlotIndex"])
                        for item in supported
                        if item.get("bestReferenceSlotIndex") is not None
                    }
                ),
                "reviewRequired": bool(
                    not accepted
                    or float(alignment["insideRatio"])
                    < float(
                        config.fragment_array_reference_anchored_review_source_inside_ratio
                    )
                    or len(supported) < physical_count
                ),
            }
        )

    gap = max(1, int(config.fragment_array_reference_anchored_parking_gap_px))
    # A rejected source frame has no trusted transform.  Preserve every original
    # pixel, but park its segmented placement units independently and compactly.
    # Keeping the original source-frame empty spacing would make high-volume
    # split-scan cases allocate an unnecessarily huge review canvas.
    parking_limit = max(base_canvas_w, 2 * gap + max(fragment.mask.shape[1] for fragment in fragments))
    rejected_fragment_indices = sorted(
        {
            int(index)
            for source_record_index in rejected_source_indices
            for index in source_bundles[source_record_index]["indices"]
        },
        key=lambda index: (
            -int(np.count_nonzero(fragments[index].mask)),
            -max(fragments[index].mask.shape),
            int(index),
        ),
    )
    parking_x = float(gap)
    parking_y = float(base_canvas_h + gap)
    row_height = 0
    maximum_parking_x = base_canvas_w
    for index in rejected_fragment_indices:
        fragment_h, fragment_w = fragments[index].mask.shape
        if parking_x > gap and parking_x + fragment_w + gap > parking_limit:
            parking_x = float(gap)
            parking_y += float(row_height + gap)
            row_height = 0
        final_poses[index] = _translation(parking_x, parking_y)
        maximum_parking_x = max(
            maximum_parking_x, int(math.ceil(parking_x + fragment_w + gap))
        )
        parking_x += float(fragment_w + gap)
        row_height = max(row_height, fragment_h)

    canvas_w = max(base_canvas_w, maximum_parking_x)
    canvas_h = (
        base_canvas_h
        if not rejected_source_indices
        else max(base_canvas_h, int(math.ceil(parking_y + row_height + gap)))
    )
    maximum_output_dimension = int(config.max_output_dimension)
    if maximum_output_dimension > 0 and (
        canvas_w > maximum_output_dimension or canvas_h > maximum_output_dimension
    ):
        raise RuntimeError(
            "reference-anchored partial output canvas가 max_output_dimension을 초과했습니다: "
            f"{canvas_w}x{canvas_h} > {maximum_output_dimension}"
        )
    if canvas_w != base_canvas_w or canvas_h != base_canvas_h:
        target = cv2.copyMakeBorder(
            target,
            0,
            canvas_h - base_canvas_h,
            0,
            canvas_w - base_canvas_w,
            cv2.BORDER_CONSTANT,
            value=0,
        )
        preview = cv2.copyMakeBorder(
            preview,
            0,
            canvas_h - base_canvas_h,
            0,
            canvas_w - base_canvas_w,
            cv2.BORDER_CONSTANT,
            value=(0, 0, 0),
        )
    resolved_poses = [
        pose if pose is not None else np.eye(3, dtype=np.float64)
        for pose in final_poses
    ]
    final_mosaic, final_mask, final_counts = _render_mosaic_first_original_pixel(
        fragments, resolved_poses, (canvas_w, canvas_h)
    )
    intersection = int(np.count_nonzero((final_mask > 0) & (target > 0)))
    union = int(np.count_nonzero((final_mask > 0) | (target > 0)))
    assembly_area = max(int(np.count_nonzero(final_mask)), 1)
    inside_ratio = float(intersection / assembly_area)
    global_iou = float(intersection / max(union, 1))
    global_boundary = float(
        _boundary_f1(
            final_mask,
            target,
            tolerance=max(1, int(config.boundary_tolerance_px)),
        )
    )
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
        "score": float(0.72 * inside_ratio + 0.18 * global_boundary + 0.10 * global_iou),
        "iou": global_iou,
        "boundaryF1": global_boundary,
        "xrayComponentCount": len(source_bundles),
        "xraySourceFrameCount": len(source_bundles),
        "xrayPlacementUnitCount": len(fragments),
        "referenceComponentCount": len(reference_records),
        "assignedComponentCount": len(accepted_source_indices),
        "assignedSourceFrameCount": len(accepted_source_indices),
        "referenceSegmentation": segmentation_debug,
        "referenceScaleEstimation": scale_debug,
        "matchingMode": "reference_anchored_source_frame_partial_n_to_one",
        "components": source_reports,
        "arrayMetrics": {
            "primaryMetric": "sourceFrameInsideReferenceRatio",
            "assemblyInsideReferenceRatio": inside_ratio,
            "referenceSlotCount": len(reference_records),
            "claimedReferenceSlotCount": len(claimed_slots),
            "unclaimedReferenceSlotCount": max(0, len(reference_records) - len(claimed_slots)),
            "assignedSourceFrameCount": len(accepted_source_indices),
            "unassignedSourceFrameCount": len(rejected_source_indices),
            "unassignedXrayComponentCount": len(rejected_source_indices),
            "ambiguousXrayComponentCount": 0,
            "assignedPlacementUnitCount": int(
                sum(len(source_bundles[index]["indices"]) for index in accepted_source_indices)
            ),
            "unassignedPlacementUnitCount": int(
                sum(len(source_bundles[index]["indices"]) for index in rejected_source_indices)
            ),
            "nToOneReferenceSlotReuseEnabled": True,
            "unassignedParkingMode": "independent_subfragment_compact_hitl",
            "renderingPolicy": "first_original_pixel_wins_no_blending_uint8_overlap_counts",
        },
    }
    debug = {
        "registrationMode": "reference_anchored_source_frames_without_cross_source_stitching",
        "captureRolePolicy": "source_frame_constellation_to_color_reference",
        "sourceFrameCount": len(source_bundles),
        "assignedSourceFrameCount": len(accepted_source_indices),
        "unassignedSourceFrameCount": len(rejected_source_indices),
        "resultComponentCount": len(source_bundles),
        "partialCaptureCandidateIndices": [
            int(fragment.index)
            for fragment in fragments
            if _is_partial_capture_candidate(fragment)
        ],
        "independentFragmentCandidateIndices": [
            int(fragment.index)
            for fragment in fragments
            if not _is_partial_capture_candidate(fragment)
            and _fragment_capture_role(fragment) != "residual_noise_group"
        ],
        "strictRegistrationCount": 0,
        "attemptedPairCount": 0,
        "candidatePairDiagnostics": [],
        "sourceFramePlacements": source_reports,
        "nToOneReferenceSlotReuseEnabled": True,
        "unassignedParkingMode": "independent_subfragment_compact_hitl",
        "renderingPolicy": "first_original_pixel_wins_no_blending_uint8_overlap_counts",
    }
    components = [[int(index) for index in bundle["indices"]] for bundle in source_bundles]
    return (
        resolved_poses,
        geometry,
        report,
        final_mosaic,
        final_mask,
        final_counts,
        components,
        debug,
        reference_slot_search_overlay,
    )


def align_fragment_array_components(
    reference_image: np.ndarray,
    reference_mask: np.ndarray,
    fragments: list[Fragment],
    raw_poses: list[np.ndarray],
    components: list[list[int]],
    config: AssemblyConfig,
) -> tuple[list[np.ndarray], SearchGeometry, dict[str, Any], np.ndarray, np.ndarray, np.ndarray]:
    reference_mask, full_reference_segmentation_debug = (
        _segment_full_fragment_array_reference(
            reference_image, reference_mask, config
        )
    )
    bundles = [_component_local_bundle(fragments, raw_poses, component) for component in components]
    total_xray_area = sum(int(bundle["area"]) for bundle in bundles)
    reference_area = int(np.count_nonzero(reference_mask))
    if total_xray_area <= 0 or reference_area <= 0:
        raise ValueError("파편 배열 reference 또는 X-ray component 면적이 0입니다.")
    if config.reference_scale_override is not None:
        reference_scale = float(config.reference_scale_override)
        reference_scale_debug = {
            "method": "explicit_override",
            "selectedScale": reference_scale,
        }
    elif bool(config.fragment_array_color_slot_robust_scale_enabled):
        robust_scale, robust_debug = _estimate_reference_scale_from_shape_anchors(
            reference_mask,
            bundles,
            config,
        )
        if robust_scale is not None:
            reference_scale = float(robust_scale)
            reference_scale_debug = robust_debug
        else:
            reference_scale, fallback_debug = _estimate_reference_scale(
                reference_mask, fragments, total_xray_area, config
            )
            reference_scale_debug = {
                **fallback_debug,
                "shapeAnchorFallback": robust_debug,
            }
    else:
        reference_scale, reference_scale_debug = _estimate_reference_scale(
            reference_mask, fragments, total_xray_area, config
        )
    reference_h, reference_w = reference_mask.shape
    scaled_w = max(1, int(round(reference_w * reference_scale)))
    scaled_h = max(1, int(round(reference_h * reference_scale)))
    scaled_reference = cv2.resize(
        reference_mask, (scaled_w, scaled_h), interpolation=cv2.INTER_NEAREST
    )
    scaled_preview = cv2.resize(
        normalize_preview(reference_image),
        (scaled_w, scaled_h),
        interpolation=cv2.INTER_AREA if reference_scale < 1.0 else cv2.INTER_LINEAR,
    )
    largest_diagonal = max(
        int(math.ceil(math.hypot(bundle["mask"].shape[1], bundle["mask"].shape[0])))
        for bundle in bundles
    )
    margin = max(
        20,
        int(round(max(scaled_w, scaled_h, largest_diagonal) * config.canvas_margin_ratio)),
    )
    base_canvas_w = max(scaled_w, largest_diagonal) + 2 * margin
    base_canvas_h = max(scaled_h, largest_diagonal) + 2 * margin
    target = np.zeros((base_canvas_h, base_canvas_w), dtype=np.uint8)
    preview = np.zeros((base_canvas_h, base_canvas_w, 3), dtype=np.uint8)
    target_x = (base_canvas_w - scaled_w) // 2
    target_y = (base_canvas_h - scaled_h) // 2
    target[target_y : target_y + scaled_h, target_x : target_x + scaled_w] = scaled_reference
    preview[target_y : target_y + scaled_h, target_x : target_x + scaled_w] = scaled_preview

    reference_records = _compact_reference_slot_records(
        target,
        minimum_area_ratio=float(config.fragment_array_reference_min_component_area_ratio),
        config=config,
    )
    if bool(config.fragment_array_color_slot_matching_enabled):
        (
            final_poses,
            component_reports,
            unassigned,
            occupied,
            claimed_slots,
            matching_debug,
        ) = _match_bundles_to_color_slots(
            fragments,
            bundles,
            reference_records,
            target,
            config,
        )
    else:
        occupied = np.zeros_like(target)
        final_poses = [None] * len(fragments)
        component_reports = {}
        unassigned = []
        claimed_slots: set[int] = set()
        matching_debug = {"mode": "legacy_whole_array_search"}

        for xray_index in sorted(
            range(len(bundles)), key=lambda index: bundles[index]["area"], reverse=True
        ):
            bundle = bundles[xray_index]
            capture_metadata = _component_capture_metadata(
                fragments, bundle["indices"]
            )
            alignment = _align_bundle_to_array(bundle["mask"], target, occupied, config)
            placed_mask = alignment["placedMask"]
            slot_assignments = _reference_slot_assignments(
                placed_mask, reference_records, config
            )
            primary_slot = (
                max(
                    slot_assignments,
                    key=lambda item: (
                        int(item["intersectionPx"]),
                        float(item["slotCoverage"]),
                    ),
                )
                if slot_assignments
                else None
            )
            accepted = bool(
                alignment["insideRatio"] >= float(config.fragment_array_min_inside_ratio)
                and alignment["occupiedOverlapRatio"]
                <= float(config.fragment_array_max_occupied_overlap_ratio)
                and primary_slot is not None
            )
            review_required = bool(
                not accepted
                or alignment["insideRatio"]
                < float(config.fragment_array_review_inside_ratio)
                or alignment["candidateScoreMargin"]
                < float(config.fragment_array_min_candidate_score_margin)
                or alignment["responseMargin"]
                < float(config.fragment_array_min_response_margin)
                or len(slot_assignments) > 1
            )
            if not accepted:
                unassigned.append(xray_index)
                component_reports[xray_index] = {
                    "xrayComponentIndex": xray_index,
                    "fragmentIndices": bundle["indices"],
                    **capture_metadata,
                    "status": "unassigned_parked_for_hitl",
                    "alignment": {
                        key: value
                        for key, value in alignment.items()
                        if key not in {"transform", "placedMask"}
                    },
                    "referenceSlotAssignments": [],
                    "candidateReferenceSlotAssignments": slot_assignments,
                    "reviewRequired": True,
                }
                continue

            transform = alignment["transform"]
            for fragment_index in bundle["indices"]:
                final_poses[fragment_index] = (
                    transform @ bundle["poses"][fragment_index]
                )
            occupied[placed_mask > 0] = 255
            primary_slot_index = int(primary_slot["referenceSlotIndex"])
            primary_record = reference_records[primary_slot_index]
            slot_x, slot_y, slot_width, slot_height = primary_record["bbox"]
            primary_slot_mask = primary_record["mask"] > 0
            occupied[
                slot_y : slot_y + slot_height,
                slot_x : slot_x + slot_width,
            ][primary_slot_mask] = 255
            claimed_slots.add(primary_slot_index)
            component_reports[xray_index] = {
                "xrayComponentIndex": xray_index,
                "fragmentIndices": bundle["indices"],
                **capture_metadata,
                "status": "assigned_to_fragment_array",
                "alignment": {
                    key: value
                    for key, value in alignment.items()
                    if key not in {"transform", "placedMask"}
                },
                "referenceSlotAssignments": [primary_slot],
                "candidateReferenceSlotAssignments": slot_assignments,
                "referenceSlotIndices": [primary_slot_index],
                "primaryReferenceSlotIndex": primary_slot_index,
                "reviewRequired": review_required,
            }

    # Park unassigned components below the reference in compact shelf rows. A
    # single vertical parking column can create a very tall, memory-heavy canvas
    # for large arrays while providing no benefit to Konva review.
    gap = 20
    parking_row_limit = max(
        base_canvas_w,
        int(config.max_output_dimension) if int(config.max_output_dimension) > 0 else base_canvas_w,
    )
    parking_x = float(gap)
    parking_y = float(base_canvas_h + gap)
    row_height = 0
    maximum_parking_x = base_canvas_w
    for xray_index in unassigned:
        bundle = bundles[xray_index]
        bundle_h, bundle_w = bundle["mask"].shape
        if parking_x > gap and parking_x + bundle_w + gap > parking_row_limit:
            parking_x = float(gap)
            parking_y += float(row_height + gap)
            row_height = 0
        transform = _translation(parking_x, parking_y)
        for fragment_index in bundle["indices"]:
            final_poses[fragment_index] = transform @ bundle["poses"][fragment_index]
        maximum_parking_x = max(
            maximum_parking_x, int(math.ceil(parking_x + bundle_w + gap))
        )
        parking_x += float(bundle_w + gap)
        row_height = max(row_height, bundle_h)

    canvas_w = max(base_canvas_w, maximum_parking_x)
    canvas_h = (
        base_canvas_h
        if not unassigned
        else max(base_canvas_h, int(math.ceil(parking_y + row_height + gap)))
    )
    maximum_output_dimension = int(config.max_output_dimension)
    if maximum_output_dimension > 0 and (
        canvas_w > maximum_output_dimension or canvas_h > maximum_output_dimension
    ):
        raise RuntimeError(
            "파편 배열 output canvas가 max_output_dimension을 초과했습니다: "
            f"{canvas_w}x{canvas_h} > {maximum_output_dimension}"
        )
    if canvas_w != base_canvas_w or canvas_h != base_canvas_h:
        target = cv2.copyMakeBorder(
            target,
            0,
            canvas_h - base_canvas_h,
            0,
            canvas_w - base_canvas_w,
            cv2.BORDER_CONSTANT,
            value=0,
        )
        preview = cv2.copyMakeBorder(
            preview,
            0,
            canvas_h - base_canvas_h,
            0,
            canvas_w - base_canvas_w,
            cv2.BORDER_CONSTANT,
            value=(0, 0, 0),
        )

    resolved_poses = [
        pose if pose is not None else np.eye(3, dtype=np.float64)
        for pose in final_poses
    ]
    final_mosaic, final_mask, final_counts = _render_mosaic_local(
        fragments, resolved_poses, (canvas_w, canvas_h)
    )
    intersection = int(np.count_nonzero((final_mask > 0) & (target > 0)))
    union = int(np.count_nonzero((final_mask > 0) | (target > 0)))
    global_iou = intersection / max(union, 1)
    global_boundary = _boundary_f1(
        final_mask,
        target,
        tolerance=max(1, int(config.boundary_tolerance_px)),
    )
    assembly_area = max(int(np.count_nonzero(final_mask)), 1)
    inside_ratio = intersection / assembly_area
    claimed_slots = {
        item["referenceSlotIndex"]
        for report in component_reports.values()
        for item in report.get("referenceSlotAssignments", [])
    }
    ambiguous_count = sum(
        bool(report.get("reviewRequired")) for report in component_reports.values()
    )

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
        "score": float(0.72 * inside_ratio + 0.18 * global_boundary + 0.10 * global_iou),
        "iou": float(global_iou),
        "boundaryF1": float(global_boundary),
        "xrayComponentCount": len(bundles),
        "referenceComponentCount": len(reference_records),
        "assignedComponentCount": len(bundles) - len(unassigned),
        "referenceSegmentation": full_reference_segmentation_debug,
        "referenceScaleEstimation": reference_scale_debug,
        "matchingMode": matching_debug.get("mode"),
        "matchingDebug": matching_debug,
        "components": [component_reports[index] for index in range(len(bundles))],
        "arrayMetrics": {
            "primaryMetric": "assemblyInsideReferenceRatio",
            "assemblyInsideReferenceRatio": float(inside_ratio),
            "referenceSlotCount": len(reference_records),
            "claimedReferenceSlotCount": len(claimed_slots),
            "unclaimedReferenceSlotCount": max(0, len(reference_records) - len(claimed_slots)),
            "unassignedXrayComponentCount": len(unassigned),
            "ambiguousXrayComponentCount": ambiguous_count,
        },
    }
    return resolved_poses, geometry, report, final_mosaic, final_mask, final_counts


def stitch_fragment_array(
    fragments: list[Fragment],
    reference_image: np.ndarray,
    reference_mask: np.ndarray,
    config: AssemblyConfig,
    route: RouteDecision,
) -> dict[str, Any]:
    if bool(config.fragment_array_reference_anchored_partial_enabled):
        (
            final_poses,
            geometry,
            alignment_score,
            final_mosaic,
            final_mask,
            final_counts,
            components,
            registration_debug,
            reference_slot_search_overlay,
        ) = align_fragment_array_source_frames_to_reference(
            reference_image,
            reference_mask,
            fragments,
            config,
        )
        registrations: list[PairRegistration] = []
        tree_edges: list[PairRegistration] = []
        diagnostic_overlay = None
        placement_mode = "reference_anchored_partial_source_frames"
    else:
        registrations, tree_edges, raw_poses, components, registration_debug = _registration_components(
            fragments, config, route
        )
        if bool(config.fragment_array_reference_matching_enabled):
            (
                final_poses,
                geometry,
                alignment_score,
                final_mosaic,
                final_mask,
                final_counts,
            ) = align_fragment_array_components(
                reference_image,
                reference_mask,
                fragments,
                raw_poses,
                components,
                config,
            )
            diagnostic_overlay = None
            if bool(config.fragment_array_color_slot_voronoi_search_enabled):
                overlay_records = _compact_reference_slot_records(
                    geometry.target_mask_full,
                    minimum_area_ratio=float(
                        config.fragment_array_reference_min_component_area_ratio
                    ),
                    config=config,
                )
                reference_slot_search_overlay = _render_reference_slot_search_overlay(
                    geometry.target_preview_full,
                    overlay_records,
                )
            else:
                reference_slot_search_overlay = None
            placement_mode = "color_fragment_array_reference_matching"
        else:
            (
                final_poses,
                geometry,
                alignment_score,
                final_mosaic,
                final_mask,
                final_counts,
                diagnostic_overlay,
            ) = neutral_pack_fragment_array_components(
                fragments,
                raw_poses,
                components,
                tree_edges,
                config,
            )
            placement_mode = "neutral_component_packing"
            reference_slot_search_overlay = None
    registration_debug["placementMode"] = placement_mode
    return {
        "registrations": registrations,
        "registrationDebug": registration_debug,
        "treeEdges": tree_edges,
        "components": components,
        "mosaicPoses": final_poses,
        "initialMosaic": final_mosaic.copy(),
        "initialMask": final_mask.copy(),
        "initialCounts": final_counts.copy(),
        "globalTransform": np.eye(3, dtype=np.float64),
        "geometry": geometry,
        "alignmentScore": alignment_score,
        "finalPoses": final_poses,
        "finalMosaic": final_mosaic,
        "finalMask": final_mask,
        "finalCounts": final_counts,
        "diagnosticOverlay": diagnostic_overlay,
        "referenceSlotSearchOverlay": reference_slot_search_overlay,
        "placementMode": placement_mode,
        "owner": None,
        "placements": matrices_to_placements(fragments, final_poses),
    }
