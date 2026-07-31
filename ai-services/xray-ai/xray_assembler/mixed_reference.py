from __future__ import annotations

import math
import re
from dataclasses import replace
from functools import lru_cache
from typing import Any

import cv2
import numpy as np

from .config import AssemblyConfig
from .image_ops import normalize_preview
from .models import Fragment, SearchGeometry
from .routing import RouteDecision
from .stitching import (
    PairRegistration,
    _align_component_mask_to_target,
    _boundary_f1,
    _component_local_bundle,
    _component_mask_records,
    _largest_contour,
    _maximum_spanning_forest,
    _pose_components,
    _refine_poses_with_pose_graph,
    _translation,
    compute_pairwise_registrations,
    matrices_to_placements,
    normalize_poses_to_canvas,
    render_mosaic,
)


def _source_sequence_record(fragment: Fragment) -> tuple[str, int | None]:
    """Return a generic capture-series key and trailing numeric position.

    This is not artifact-specific hardcoding.  The numeric suffix is used only
    to preserve acquisition adjacency after image-based role classification.
    """
    stem = fragment.path.stem.strip()
    match = re.match(r"^(.*?)-\s*(\d+)$", stem)
    if match is None:
        return stem.casefold(), None
    key = re.sub(r"\s+", " ", match.group(1).strip()).casefold()
    return key, int(match.group(2))


def _source_body_evidence(fragment: Fragment) -> dict[str, Any]:
    diagnostics = fragment.diagnostics
    source_shape = diagnostics.get("source_shape", fragment.mask.shape)
    source_h, source_w = [max(1, int(value)) for value in source_shape[:2]]
    _, _, bbox_w, bbox_h = [int(value) for value in fragment.crop_bbox_xywh]
    source_area = max(source_h * source_w, 1)
    fill_ratio = float(fragment.mask_area / source_area)
    width_fraction = float(min(max(bbox_w / source_w, 0.0), 1.0))
    height_fraction = float(min(max(bbox_h / source_h, 0.0), 1.0))
    touch_sides = diagnostics.get("touch_sides", {})
    if isinstance(touch_sides, dict) and touch_sides:
        border_contact = float(sum(bool(value) for value in touch_sides.values()) / 4.0)
    else:
        border_contact = float(bool(diagnostics.get("touches_frame", False)))
    elongation = float(max(bbox_w, bbox_h) / max(min(bbox_w, bbox_h), 1))
    frame_filled = float(str(diagnostics.get("method", "")).startswith("frame_filled"))
    elongation_penalty = min(max(elongation - 1.0, 0.0) / 3.0, 1.0)
    score = (
        0.50 * fill_ratio
        + 0.20 * min(width_fraction, height_fraction)
        + 0.12 * max(width_fraction, height_fraction)
        + 0.12 * border_contact
        + 0.08 * frame_filled
        - 0.08 * elongation_penalty
    )
    series_key, sequence_number = _source_sequence_record(fragment)
    return {
        "fragmentIndex": int(fragment.index),
        "seriesKey": series_key,
        "sequenceNumber": sequence_number,
        "fillRatio": fill_ratio,
        "widthFraction": width_fraction,
        "heightFraction": height_fraction,
        "borderContact": border_contact,
        "elongation": elongation,
        "frameFilled": bool(frame_filled),
        "score": float(score),
    }


def _detect_body_like_registration_runs(
    fragments: list[Fragment], config: AssemblyConfig
) -> tuple[list[list[int]], dict[int, dict[str, Any]], dict[str, Any]]:
    evidence = [_source_body_evidence(fragment) for fragment in fragments]
    evidence_by_index = {int(item["fragmentIndex"]): item for item in evidence}
    if not evidence:
        return [], evidence_by_index, {"enabled": True, "runs": []}
    scores = np.asarray([float(item["score"]) for item in evidence], dtype=np.float32)
    if not bool(config.mixed_body_preclassification_enabled) or len(evidence) < 4:
        threshold = float(np.median(scores))
    else:
        cv2.setRNGSeed(int(config.random_seed))
        _, labels, centers = cv2.kmeans(
            scores.reshape(-1, 1),
            2,
            None,
            (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 1e-6),
            12,
            cv2.KMEANS_PP_CENTERS,
        )
        center_values = centers.reshape(-1)
        lower, upper = sorted(float(value) for value in center_values)
        threshold = float(0.5 * (lower + upper))
        # When the two clusters are nearly indistinguishable, use a robust
        # upper-half threshold instead of manufacturing a strong distinction.
        if upper - lower < 0.08:
            threshold = float(np.quantile(scores, 0.60))

    for item in evidence:
        item["bodyLike"] = bool(float(item["score"]) >= threshold)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in evidence:
        if not item["bodyLike"] or item["sequenceNumber"] is None:
            continue
        grouped.setdefault(str(item["seriesKey"]), []).append(item)

    minimum_run = max(2, int(config.mixed_body_registration_min_run_length))
    raw_runs: list[dict[str, Any]] = []
    for records in grouped.values():
        records.sort(key=lambda item: int(item["sequenceNumber"]))
        current: list[dict[str, Any]] = []
        previous: int | None = None
        previous_score: float | None = None
        for item in records:
            number = int(item["sequenceNumber"])
            score_drop = (
                previous_score is not None
                and previous_score - float(item["score"])
                >= float(config.mixed_body_run_score_drop_threshold)
                and float(item["score"])
                <= threshold + float(config.mixed_body_run_low_score_margin)
            )
            if previous is None or (number == previous + 1 and not score_drop):
                current.append(item)
            else:
                if len(current) >= minimum_run:
                    raw_runs.append(
                        {
                            "seriesKey": str(current[0]["seriesKey"]),
                            "records": current,
                        }
                    )
                current = [item]
            previous = number
            previous_score = float(item["score"])
        if len(current) >= minimum_run:
            raw_runs.append(
                {
                    "seriesKey": str(current[0]["seriesKey"]),
                    "records": current,
                }
            )

    raw_runs.sort(
        key=lambda run: (
            -len(run["records"]),
            -sum(float(item["score"]) for item in run["records"]),
            min(int(item["fragmentIndex"]) for item in run["records"]),
        )
    )
    runs = [
        [int(item["fragmentIndex"]) for item in run["records"]]
        for run in raw_runs
    ]
    for item in evidence:
        item["bodyRunIndex"] = None
        item["primaryBodyRun"] = False
        item["secondaryBodyEligible"] = False
    if raw_runs:
        primary = raw_runs[0]
        primary_records = primary["records"]
        primary_series = str(primary["seriesKey"])
        primary_numbers = [int(item["sequenceNumber"]) for item in primary_records]
        primary_min = min(primary_numbers)
        primary_max = max(primary_numbers)
        for run_index, run in enumerate(raw_runs):
            run_records = run["records"]
            run_numbers = [int(item["sequenceNumber"]) for item in run_records]
            one_missing_frame_apart = bool(
                str(run["seriesKey"]) == primary_series
                and (
                    min(run_numbers) - primary_max == 2
                    or primary_min - max(run_numbers) == 2
                )
            )
            eligible = bool(run_index == 0 or one_missing_frame_apart)
            for item in run_records:
                item["bodyRunIndex"] = int(run_index)
                item["primaryBodyRun"] = bool(run_index == 0)
                item["secondaryBodyEligible"] = eligible
    return runs, evidence_by_index, {
        "enabled": bool(config.mixed_body_preclassification_enabled),
        "threshold": float(threshold),
        "scoreDropThreshold": float(config.mixed_body_run_score_drop_threshold),
        "lowScoreMargin": float(config.mixed_body_run_low_score_margin),
        "bodyLikeSourceCount": int(sum(bool(item["bodyLike"]) for item in evidence)),
        "runs": [list(map(int, run)) for run in runs],
        "sources": evidence,
    }


def _mixed_registration_config(config: AssemblyConfig) -> AssemblyConfig:
    return replace(
        config,
        pair_analysis_max_dimension=min(
            int(config.pair_analysis_max_dimension),
            int(config.mixed_body_registration_max_dimension),
        ),
        sift_features=min(
            int(config.sift_features),
            int(config.mixed_body_registration_sift_features),
        ),
        phase_angle_radius_deg=min(
            int(config.phase_angle_radius_deg),
            int(config.mixed_body_registration_phase_angle_radius_deg),
        ),
        pair_index_window=max(1, int(config.mixed_body_registration_pair_index_window)),
        pair_all_limit=1,
        frame_extended_sift_enabled=False,
    )


def _mixed_component_alignment_config(config: AssemblyConfig) -> AssemblyConfig:
    return replace(
        config,
        global_alignment_max_dimension=min(
            int(config.global_alignment_max_dimension),
            int(config.mixed_component_alignment_max_dimension),
        ),
        global_angle_radius_deg=min(
            int(config.global_angle_radius_deg),
            int(config.mixed_component_alignment_angle_radius_deg),
        ),
        global_angle_step_deg=max(
            1,
            int(config.mixed_component_alignment_angle_step_deg),
        ),
        global_refine_angle_step_deg=max(
            0.5,
            float(config.mixed_component_alignment_refine_step_deg),
        ),
    )


def _role_aware_pairwise_registrations(
    fragments: list[Fragment], config: AssemblyConfig
) -> tuple[list[PairRegistration], dict[str, Any], dict[int, dict[str, Any]]]:
    runs, evidence_by_index, classification_debug = _detect_body_like_registration_runs(
        fragments, config
    )
    if not bool(config.mixed_body_preclassification_enabled):
        registrations, debug = compute_pairwise_registrations(fragments, config)
        debug["mixedBodyPreclassification"] = classification_debug
        return registrations, debug, evidence_by_index

    registrations: list[PairRegistration] = []
    run_debug: list[dict[str, Any]] = []
    fast_config = _mixed_registration_config(config)
    for run_index, original_indices in enumerate(runs):
        local_fragments = [
            replace(fragments[original_index], index=local_index)
            for local_index, original_index in enumerate(original_indices)
        ]
        local_registrations, local_debug = compute_pairwise_registrations(
            local_fragments, fast_config
        )
        remapped = [
            replace(
                edge,
                first_index=int(original_indices[edge.first_index]),
                second_index=int(original_indices[edge.second_index]),
            )
            for edge in local_registrations
        ]
        registrations.extend(remapped)
        run_debug.append(
            {
                "runIndex": int(run_index),
                "fragmentIndices": list(map(int, original_indices)),
                "registrationCount": len(remapped),
                "registrationMode": local_debug.get("registrationMode"),
                "sequenceBreaks": local_debug.get("sequenceBreaks", []),
                "forestInput": [edge.to_dict() for edge in remapped],
            }
        )
    return registrations, {
        "registrationMode": "mixed_body_like_runs_only",
        "longestScannerRun": max((len(run) for run in runs), default=0),
        "candidateAlternatives": {},
        "sequenceResolution": [],
        "sequenceBreaks": [],
        "extendedSiftAnchors": [],
        "cycleConsistency": [],
        "mixedBodyPreclassification": classification_debug,
        "mixedRegistrationRuns": run_debug,
        "excludedFromInitialRegistration": sorted(
            set(range(len(fragments))) - {index for run in runs for index in run}
        ),
    }, evidence_by_index


def _bundle_body_source_fraction(
    bundle: dict[str, Any], source_evidence: dict[int, dict[str, Any]] | None
) -> float:
    if not source_evidence:
        return 0.0
    values = [
        float(
            bool(
                source_evidence.get(int(index), {}).get(
                    "secondaryBodyEligible",
                    source_evidence.get(int(index), {}).get("bodyLike", False),
                )
            )
        )
        for index in bundle["indices"]
    ]
    return float(sum(values) / max(len(values), 1))


def _body_candidate_score(
    bundle: dict[str, Any],
    total_area: int,
    total_fragments: int,
    fragments: list[Fragment],
    source_evidence: dict[int, dict[str, Any]] | None = None,
) -> float:
    indices = [int(index) for index in bundle["indices"]]
    area_fraction = float(bundle["area"]) / max(total_area, 1)
    fragment_fraction = len(indices) / max(total_fragments, 1)
    scanner_fraction = sum(
        bool(fragments[index].diagnostics.get("touches_frame", False))
        for index in indices
    ) / max(len(indices), 1)
    body_source_fraction = _bundle_body_source_fraction(bundle, source_evidence)
    return float(
        0.55 * area_fraction
        + 0.20 * fragment_fraction
        + 0.07 * scanner_fraction
        + 0.18 * body_source_fraction
    )


def _shape_cost(first_mask: np.ndarray, second_mask: np.ndarray) -> float:
    first = _largest_contour(first_mask)
    second = _largest_contour(second_mask)
    if first is None or second is None:
        return 2.0
    return min(float(cv2.matchShapes(first, second, cv2.CONTOURS_MATCH_I1, 0.0)), 4.0)


def _mixed_assignment_cost(
    bundle: dict[str, Any],
    reference: dict[str, Any],
    body_bundle: dict[str, Any],
    body_reference: dict[str, Any],
) -> dict[str, float]:
    xray_relative_area = float(bundle["area"]) / max(float(body_bundle["area"]), 1.0)
    reference_relative_area = float(reference["area"]) / max(float(body_reference["area"]), 1.0)
    area_cost = abs(math.log(max(xray_relative_area, 1e-9) / max(reference_relative_area, 1e-9)))
    aspect_cost = abs(
        math.log(max(float(bundle["aspect"]), 1e-6) / max(float(reference["aspect"]), 1e-6))
    )
    shape_cost = _shape_cost(bundle["mask"], reference["mask"])
    total = 1.10 * area_cost + 0.38 * aspect_cost + 0.30 * shape_cost
    return {
        "total": float(total),
        "relativeArea": float(area_cost),
        "aspect": float(aspect_cost),
        "shape": float(shape_cost),
    }


def _place_mask(mask: np.ndarray, transform: np.ndarray, canvas_size: tuple[int, int]) -> np.ndarray:
    width, height = canvas_size
    return cv2.warpAffine(
        (mask > 0).astype(np.uint8) * 255,
        transform[:2].astype(np.float32),
        (int(width), int(height)),
        flags=cv2.INTER_NEAREST,
    )


def _candidate_alignment(
    bundle: dict[str, Any],
    reference: dict[str, Any],
    body_bundle: dict[str, Any],
    body_reference: dict[str, Any],
    occupied_mask: np.ndarray,
    canvas_size: tuple[int, int],
    config: AssemblyConfig,
) -> dict[str, Any]:
    transform, alignment = _align_component_mask_to_target(bundle["mask"], reference["mask"], config)
    placed = _place_mask(bundle["mask"], transform, canvas_size)
    placed_pixels = max(int(np.count_nonzero(placed)), 1)
    intersection = int(np.count_nonzero((placed > 0) & (reference["mask"] > 0)))
    inside_ratio = intersection / placed_pixels
    slot_coverage = intersection / max(int(reference["area"]), 1)
    occupied_overlap_ratio = float(np.count_nonzero((placed > 0) & (occupied_mask > 0))) / placed_pixels
    costs = _mixed_assignment_cost(bundle, reference, body_bundle, body_reference)
    objective = (
        0.58 * float(alignment.get("score", 0.0))
        + 0.24 * inside_ratio
        + 0.08 * min(slot_coverage, 1.0)
        - float(config.mixed_occupied_overlap_weight) * occupied_overlap_ratio
        - 0.10 * min(float(costs["total"]) / max(float(config.mixed_max_assignment_cost), 1e-6), 1.5)
    )
    accepted = bool(
        float(alignment.get("score", 0.0))
        >= max(
            float(config.mixed_min_alignment_score),
            float(config.mixed_detached_min_alignment_score),
        )
        and inside_ratio
        >= max(
            float(config.mixed_min_inside_ratio),
            float(config.mixed_detached_min_inside_ratio),
        )
        and float(costs["total"])
        <= min(
            float(config.mixed_max_assignment_cost),
            float(config.mixed_detached_max_assignment_cost),
        )
        and occupied_overlap_ratio <= float(config.mixed_max_occupied_overlap_ratio)
    )
    return {
        "transform": transform,
        "alignment": alignment,
        "insideRatio": float(inside_ratio),
        "slotCoverage": float(slot_coverage),
        "occupiedOverlapRatio": float(occupied_overlap_ratio),
        "assignmentCost": costs,
        "objective": float(objective),
        "accepted": accepted,
    }


def _maximum_utility_assignment(
    xray_indices: list[int],
    reference_indices: list[int],
    candidates: dict[tuple[int, int], dict[str, Any]],
    greedy_threshold: int = 16,
) -> list[tuple[int, int]]:
    if max(len(xray_indices), len(reference_indices)) > max(1, int(greedy_threshold)):
        ranked = sorted(
            (
                (float(candidate["objective"]), int(xray_index), int(reference_index))
                for (xray_index, reference_index), candidate in candidates.items()
                if candidate.get("accepted", False)
            ),
            reverse=True,
        )
        used_xray: set[int] = set()
        used_reference: set[int] = set()
        selected: list[tuple[int, int]] = []
        for _, xray_index, reference_index in ranked:
            if xray_index in used_xray or reference_index in used_reference:
                continue
            used_xray.add(xray_index)
            used_reference.add(reference_index)
            selected.append((xray_index, reference_index))
        return selected

    reference_bits = {reference_index: bit for bit, reference_index in enumerate(reference_indices)}

    @lru_cache(maxsize=None)
    def solve(position: int, used_mask: int) -> tuple[float, tuple[tuple[int, int], ...]]:
        if position >= len(xray_indices):
            return 0.0, ()
        xray_index = xray_indices[position]
        best_score, best_pairs = solve(position + 1, used_mask)
        for reference_index in reference_indices:
            bit = 1 << reference_bits[reference_index]
            if used_mask & bit:
                continue
            candidate = candidates.get((xray_index, reference_index))
            if candidate is None or not candidate["accepted"]:
                continue
            tail_score, tail_pairs = solve(position + 1, used_mask | bit)
            score = float(candidate["objective"]) + tail_score
            if score > best_score + 1e-12:
                best_score = score
                best_pairs = ((xray_index, reference_index),) + tail_pairs
        return best_score, best_pairs

    return list(solve(0, 0)[1])


def align_mixed_components_to_reference(
    reference_image: np.ndarray,
    reference_mask: np.ndarray,
    fragments: list[Fragment],
    raw_poses: list[np.ndarray],
    components: list[list[int]],
    config: AssemblyConfig,
    source_evidence: dict[int, dict[str, Any]] | None = None,
) -> tuple[list[np.ndarray], SearchGeometry, dict[str, Any], np.ndarray, np.ndarray, np.ndarray]:
    bundles = [_component_local_bundle(fragments, raw_poses, component) for component in components]
    if not bundles:
        raise ValueError("X-ray registration component가 없습니다.")
    original_reference_records = _component_mask_records(
        reference_mask,
        minimum_area_ratio=float(config.mixed_reference_min_component_area_ratio),
    )
    if len(original_reference_records) < 2:
        raise ValueError("혼합 모드는 본체와 별도 reference component가 모두 필요합니다.")

    total_xray_area = sum(int(bundle["area"]) for bundle in bundles)
    body_scores = [
        _body_candidate_score(
            bundle,
            total_xray_area,
            len(fragments),
            fragments,
            source_evidence,
        )
        for bundle in bundles
    ]
    body_order = sorted(range(len(bundles)), key=lambda index: body_scores[index], reverse=True)
    body_xray_index = int(body_order[0])
    body_dominance_ratio = float(
        body_scores[body_order[0]] / max(body_scores[body_order[1]], 1e-9)
    ) if len(body_order) > 1 else float("inf")
    body_bundle = bundles[body_xray_index]
    body_reference_original = original_reference_records[0]

    reference_scale = (
        float(config.reference_scale_override)
        if config.reference_scale_override is not None
        else math.sqrt(
            float(body_bundle["area"])
            / max(float(body_reference_original["area"]) * float(config.reference_fill_ratio), 1.0)
        )
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
    margin = max(
        20,
        int(round(max(scaled_w, scaled_h, largest_component_diagonal) * float(config.canvas_margin_ratio))),
    )
    canvas_w = max(scaled_w, largest_component_diagonal) + 2 * margin
    canvas_h = max(scaled_h, largest_component_diagonal) + 2 * margin
    target = np.zeros((canvas_h, canvas_w), dtype=np.uint8)
    preview = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    target_x = (canvas_w - scaled_w) // 2
    target_y = (canvas_h - scaled_h) // 2
    target[target_y : target_y + scaled_h, target_x : target_x + scaled_w] = scaled_reference
    preview[target_y : target_y + scaled_h, target_x : target_x + scaled_w] = scaled_preview

    reference_records = _component_mask_records(
        target,
        minimum_area_ratio=float(config.mixed_reference_min_component_area_ratio),
    )
    if len(reference_records) < 2:
        raise ValueError("스케일 적용 후 혼합 reference component가 2개 미만입니다.")
    body_reference_index = 0
    body_reference = reference_records[body_reference_index]
    body_transform, body_alignment = _align_component_mask_to_target(
        body_bundle["mask"], body_reference["mask"], config
    )
    component_alignment_config = _mixed_component_alignment_config(config)
    body_placed_mask = _place_mask(body_bundle["mask"], body_transform, (canvas_w, canvas_h))
    body_pixels = max(int(np.count_nonzero(body_placed_mask)), 1)
    body_inside_ratio = float(
        np.count_nonzero((body_placed_mask > 0) & (body_reference["mask"] > 0))
    ) / body_pixels

    final_poses: list[np.ndarray | None] = [None] * len(fragments)
    for fragment_index in body_bundle["indices"]:
        final_poses[fragment_index] = body_transform @ body_bundle["poses"][fragment_index]

    body_review = bool(
        body_dominance_ratio < float(config.mixed_body_min_dominance_ratio)
        or body_inside_ratio < float(config.mixed_review_inside_ratio)
        or float(body_alignment.get("score", 0.0)) < float(config.mixed_review_alignment_score)
    )
    component_reports: list[dict[str, Any]] = [
        {
            "xrayComponentIndex": body_xray_index,
            "referenceComponentIndex": body_reference_index,
            "fragmentIndices": body_bundle["indices"],
            "componentRole": "primary_body",
            "referenceRole": "primary_body",
            "assignmentCost": 0.0,
            "alignment": body_alignment,
            "insideReferenceRatio": float(body_inside_ratio),
            "bodyCandidateScore": float(body_scores[body_xray_index]),
            "bodyLikeSourceFraction": float(
                _bundle_body_source_fraction(body_bundle, source_evidence)
            ),
            "bodyDominanceRatio": float(body_dominance_ratio),
            "status": "assigned_to_primary_reference_component",
            "reviewRequired": body_review,
        }
    ]

    assigned_xray: set[int] = {body_xray_index}
    assigned_reference: set[int] = {body_reference_index}
    occupied_mask = body_placed_mask.copy()

    secondary_candidates: list[dict[str, Any]] = []
    for xray_index, bundle in enumerate(bundles):
        if xray_index == body_xray_index:
            continue
        source_fraction = _bundle_body_source_fraction(bundle, source_evidence)
        if source_fraction < float(config.mixed_secondary_body_min_source_fraction):
            continue
        transform, alignment = _align_component_mask_to_target(
            bundle["mask"], body_reference["mask"], component_alignment_config
        )
        placed = _place_mask(bundle["mask"], transform, (canvas_w, canvas_h))
        pixels = max(int(np.count_nonzero(placed)), 1)
        inside = int(
            np.count_nonzero((placed > 0) & (body_reference["mask"] > 0))
        ) / pixels
        overlap = int(np.count_nonzero((placed > 0) & (occupied_mask > 0))) / pixels
        new_coverage = int(
            np.count_nonzero(
                (placed > 0)
                & (body_reference["mask"] > 0)
                & (occupied_mask == 0)
            )
        ) / pixels
        objective = (
            0.40 * float(alignment.get("score", 0.0))
            + 0.27 * float(inside)
            + 0.20 * float(new_coverage)
            + 0.13 * float(source_fraction)
            - 0.18 * float(overlap)
        )
        secondary_candidates.append(
            {
                "xrayIndex": int(xray_index),
                "transform": transform,
                "alignment": alignment,
                "insideRatio": float(inside),
                "overlapRatio": float(overlap),
                "newCoverageRatio": float(new_coverage),
                "sourceFraction": float(source_fraction),
                "objective": float(objective),
            }
        )

    secondary_candidates.sort(key=lambda item: float(item["objective"]), reverse=True)
    secondary_body_count = 0
    for candidate in secondary_candidates:
        xray_index = int(candidate["xrayIndex"])
        if xray_index in assigned_xray:
            continue
        placed = _place_mask(
            bundles[xray_index]["mask"],
            candidate["transform"],
            (canvas_w, canvas_h),
        )
        pixels = max(int(np.count_nonzero(placed)), 1)
        overlap = int(np.count_nonzero((placed > 0) & (occupied_mask > 0))) / pixels
        new_coverage = int(
            np.count_nonzero(
                (placed > 0)
                & (body_reference["mask"] > 0)
                & (occupied_mask == 0)
            )
        ) / pixels
        accepted = bool(
            float(candidate["alignment"].get("score", 0.0))
            >= float(config.mixed_secondary_body_min_alignment_score)
            and float(candidate["insideRatio"])
            >= float(config.mixed_secondary_body_min_inside_ratio)
            and overlap <= float(config.mixed_secondary_body_max_overlap_ratio)
            and new_coverage
            >= float(config.mixed_secondary_body_min_new_coverage_ratio)
        )
        if not accepted:
            continue
        bundle = bundles[xray_index]
        for fragment_index in bundle["indices"]:
            final_poses[fragment_index] = (
                candidate["transform"] @ bundle["poses"][fragment_index]
            )
        occupied_mask[placed > 0] = 255
        assigned_xray.add(xray_index)
        secondary_body_count += 1
        component_reports.append(
            {
                "xrayComponentIndex": xray_index,
                "referenceComponentIndex": body_reference_index,
                "fragmentIndices": bundle["indices"],
                "componentRole": "secondary_body",
                "referenceRole": "primary_body",
                "assignmentCost": None,
                "alignment": candidate["alignment"],
                "insideReferenceRatio": float(candidate["insideRatio"]),
                "occupiedOverlapRatio": float(overlap),
                "newBodyCoverageRatio": float(new_coverage),
                "bodyLikeSourceFraction": float(candidate["sourceFraction"]),
                "candidateObjective": float(candidate["objective"]),
                "status": "assigned_to_primary_reference_component_secondary",
                "reviewRequired": bool(
                    float(candidate["insideRatio"])
                    < float(config.mixed_review_inside_ratio)
                    or float(candidate["alignment"].get("score", 0.0))
                    < float(config.mixed_review_alignment_score)
                ),
            }
        )

    xray_indices = [
        index for index in range(len(bundles)) if index not in assigned_xray
    ]
    body_occupied_mask = occupied_mask.copy()
    reference_indices = list(range(1, len(reference_records)))
    candidates: dict[tuple[int, int], dict[str, Any]] = {}
    for xray_index in xray_indices:
        ranked_reference_indices = sorted(
            reference_indices,
            key=lambda reference_index: float(
                _mixed_assignment_cost(
                    bundles[xray_index],
                    reference_records[reference_index],
                    body_bundle,
                    body_reference,
                )["total"]
            ),
        )[: max(1, int(config.mixed_detached_candidate_limit))]
        for reference_index in ranked_reference_indices:
            candidates[(xray_index, reference_index)] = _candidate_alignment(
                bundles[xray_index],
                reference_records[reference_index],
                body_bundle,
                body_reference,
                body_placed_mask,
                (canvas_w, canvas_h),
                component_alignment_config,
            )

    assignments = _maximum_utility_assignment(
        xray_indices,
        reference_indices,
        candidates,
        greedy_threshold=int(config.mixed_large_assignment_greedy_threshold),
    )
    ambiguous_count = 0
    for xray_index, reference_index in assignments:
        bundle = bundles[xray_index]
        candidate = candidates[(xray_index, reference_index)]
        candidate_scores = sorted(
            (
                float(value["objective"])
                for (candidate_xray, _), value in candidates.items()
                if candidate_xray == xray_index and value["accepted"]
            ),
            reverse=True,
        )
        score_margin = (
            candidate_scores[0] - candidate_scores[1]
            if len(candidate_scores) > 1
            else candidate_scores[0] if candidate_scores else 0.0
        )
        review_required = bool(
            candidate["insideRatio"] < float(config.mixed_review_inside_ratio)
            or score_margin < float(config.mixed_min_candidate_score_margin)
            or float(candidate["alignment"].get("score", 0.0)) < float(config.mixed_review_alignment_score)
        )
        if score_margin < float(config.mixed_min_candidate_score_margin):
            ambiguous_count += 1
        for fragment_index in bundle["indices"]:
            final_poses[fragment_index] = candidate["transform"] @ bundle["poses"][fragment_index]
        selected_placed_mask = _place_mask(
            bundle["mask"],
            candidate["transform"],
            (canvas_w, canvas_h),
        )
        occupied_mask[selected_placed_mask > 0] = 255
        assigned_xray.add(xray_index)
        assigned_reference.add(reference_index)
        component_reports.append(
            {
                "xrayComponentIndex": xray_index,
                "referenceComponentIndex": reference_index,
                "fragmentIndices": bundle["indices"],
                "componentRole": "independent_fragment",
                "referenceRole": "detached_fragment_slot",
                "assignmentCost": float(candidate["assignmentCost"]["total"]),
                "assignmentCostBreakdown": candidate["assignmentCost"],
                "alignment": candidate["alignment"],
                "insideReferenceRatio": float(candidate["insideRatio"]),
                "referenceSlotCoverage": float(candidate["slotCoverage"]),
                "occupiedOverlapRatio": float(candidate["occupiedOverlapRatio"]),
                "candidateObjective": float(candidate["objective"]),
                "candidateScoreMargin": float(score_margin),
                "status": "assigned_to_detached_reference_component",
                "reviewRequired": review_required,
            }
        )

    parking_gap = max(8, int(config.mixed_review_panel_gap_px))
    parking_x = float(canvas_w + parking_gap)
    parking_y = float(parking_gap)
    parking_column_width = 0
    required_canvas_width = canvas_w
    required_canvas_height = canvas_h
    unassigned_indices = [
        xray_index for xray_index in xray_indices if xray_index not in assigned_xray
    ]
    unassigned_indices.sort(
        key=lambda index: (
            -int(bundles[index]["area"]),
            int(index),
        )
    )
    for xray_index in unassigned_indices:
        bundle = bundles[xray_index]
        bundle_height, bundle_width = bundle["mask"].shape
        if (
            parking_y > parking_gap
            and parking_y + bundle_height + parking_gap > canvas_h
        ):
            parking_x += float(parking_column_width + parking_gap)
            parking_y = float(parking_gap)
            parking_column_width = 0
        transform = _translation(parking_x, parking_y)
        for fragment_index in bundle["indices"]:
            final_poses[fragment_index] = transform @ bundle["poses"][fragment_index]
        parking_y += float(bundle_height + parking_gap)
        parking_column_width = max(parking_column_width, int(bundle_width))
        required_canvas_width = max(
            required_canvas_width,
            int(math.ceil(parking_x + bundle_width + parking_gap)),
        )
        required_canvas_height = max(
            required_canvas_height,
            int(math.ceil(parking_y + parking_gap)),
        )
        accepted_candidates = [
            {
                "referenceComponentIndex": reference_index,
                "objective": float(candidate["objective"]),
                "insideReferenceRatio": float(candidate["insideRatio"]),
                "alignmentScore": float(candidate["alignment"].get("score", 0.0)),
                "assignmentCost": float(candidate["assignmentCost"]["total"]),
                "accepted": bool(candidate["accepted"]),
            }
            for (candidate_xray, reference_index), candidate in candidates.items()
            if candidate_xray == xray_index
        ]
        accepted_candidates.sort(key=lambda item: item["objective"], reverse=True)
        component_reports.append(
            {
                "xrayComponentIndex": xray_index,
                "referenceComponentIndex": None,
                "fragmentIndices": bundle["indices"],
                "componentRole": "independent_fragment",
                "referenceRole": None,
                "assignmentCost": None,
                "alignment": None,
                "status": "unassigned_parked_for_hitl",
                "candidateAlternatives": accepted_candidates[:3],
                "reviewRequired": True,
            }
        )

    extra_width = max(0, int(required_canvas_width - canvas_w))
    extra_height = max(0, int(required_canvas_height - canvas_h))
    if extra_width or extra_height:
        canvas_w += extra_width
        canvas_h += extra_height
        target = cv2.copyMakeBorder(
            target, 0, extra_height, 0, extra_width, cv2.BORDER_CONSTANT, value=0
        )
        preview = cv2.copyMakeBorder(
            preview, 0, extra_height, 0, extra_width, cv2.BORDER_CONSTANT, value=(0, 0, 0)
        )

    resolved_poses = [pose if pose is not None else np.eye(3, dtype=np.float64) for pose in final_poses]
    final_mosaic, final_mask, final_counts, _ = render_mosaic(
        fragments, resolved_poses, (canvas_w, canvas_h)
    )
    target_binary = target > 0
    final_binary = final_mask > 0
    intersection = int(np.count_nonzero(final_binary & target_binary))
    union = int(np.count_nonzero(final_binary | target_binary))
    global_iou = intersection / max(union, 1)
    global_boundary = _boundary_f1(final_mask, target, tolerance=max(1, int(config.boundary_tolerance_px)))
    assembly_pixels = max(int(np.count_nonzero(final_binary)), 1)
    inside_reference_ratio = intersection / assembly_pixels
    body_reference_binary = body_reference["mask"] > 0
    body_assembly_binary = body_occupied_mask > 0
    body_intersection = int(
        np.count_nonzero(body_assembly_binary & body_reference_binary)
    )
    body_union = int(np.count_nonzero(body_assembly_binary | body_reference_binary))
    body_iou = float(body_intersection / max(body_union, 1))
    body_reference_coverage = float(
        body_intersection / max(int(np.count_nonzero(body_reference_binary)), 1)
    )
    body_inside_ratio = float(
        body_intersection / max(int(np.count_nonzero(body_assembly_binary)), 1)
    )
    body_boundary = float(
        _boundary_f1(
            body_occupied_mask,
            body_reference["mask"],
            tolerance=max(1, int(config.boundary_tolerance_px)),
        )
    )

    search_ratio = min(1.0, float(config.search_max_dimension) / max(canvas_w, canvas_h))
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
    assigned_detached_count = len(assignments)
    unassigned_count = len(bundles) - len(assigned_xray)
    report = {
        "score": float(0.82 * body_iou + 0.18 * body_boundary),
        "iou": float(global_iou),
        "boundaryF1": float(global_boundary),
        "bodyScore": float(0.82 * body_iou + 0.18 * body_boundary),
        "bodyBoundaryF1": float(body_boundary),
        "xrayComponentCount": len(bundles),
        "referenceComponentCount": len(reference_records),
        "assignedComponentCount": len(assigned_xray),
        "components": component_reports,
        "mixedMetrics": {
            "primaryXrayComponentIndex": body_xray_index,
            "primaryReferenceComponentIndex": body_reference_index,
            "bodyDominanceRatio": float(body_dominance_ratio),
            "primaryBodyInsideReferenceRatio": float(
                component_reports[0]["insideReferenceRatio"]
            ),
            "bodyComponentCount": int(1 + secondary_body_count),
            "secondaryBodyComponentCount": int(secondary_body_count),
            "bodyIoU": float(body_iou),
            "bodyReferenceCoverageRatio": float(body_reference_coverage),
            "bodyInsideReferenceRatio": float(body_inside_ratio),
            "assignedDetachedComponentCount": int(assigned_detached_count),
            "unassignedXrayComponentCount": int(unassigned_count),
            "unusedReferenceComponentCount": int(len(reference_records) - len(assigned_reference)),
            "ambiguousXrayComponentCount": int(ambiguous_count),
            "assemblyInsideReferenceRatio": float(inside_reference_ratio),
        },
    }
    return resolved_poses, geometry, report, final_mosaic, final_mask, final_counts


def stitch_mixed_reference(
    fragments: list[Fragment],
    reference_image: np.ndarray,
    reference_mask: np.ndarray,
    config: AssemblyConfig,
    route: RouteDecision,
) -> dict[str, Any]:
    registrations, registration_debug, source_evidence = (
        _role_aware_pairwise_registrations(fragments, config)
    )
    tree_edges, forest_debug = _maximum_spanning_forest(
        len(fragments), registrations, config, fragments
    )
    registration_debug["forestSelection"] = forest_debug
    registration_debug["registrationPolicy"] = (
        "mixed_body_like_run_registration_then_multi_component_body_alignment"
    )
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
    final_poses, geometry, alignment_score, final_mosaic, final_mask, final_counts = (
        align_mixed_components_to_reference(
            reference_image,
            reference_mask,
            fragments,
            raw_poses,
            components,
            config,
            source_evidence,
        )
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
        "route": route.to_dict(),
    }
