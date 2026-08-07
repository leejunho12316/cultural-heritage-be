from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .config import AssemblyConfig
from .fragment_array import stitch_fragment_array
from .image_ops import list_images, read_image, save_json, write_image
from .mixed_reference import stitch_mixed_reference
from .routing import RouteRequest, resolve_route
from .segmentation import load_fragments, segment_reference, split_multi_object_fragments
from .stitching import make_alignment_overlay, stitch_fragments


def _write_image(path: Path, image: np.ndarray) -> None:
    write_image(path, image)




def _validate_transform_and_source_invariants(
    fragments: list[Any], final_poses: list[np.ndarray]
) -> dict[str, Any]:
    placement_keys = [str(fragment.path).casefold() for fragment in fragments]
    unique_placement_count = len(set(placement_keys))
    original_source_keys = [
        str((getattr(fragment, "source_path", None) or fragment.path)).casefold()
        for fragment in fragments
    ]
    rigid_failures: list[int] = []
    for fragment, pose in zip(fragments, final_poses):
        linear = np.asarray(pose, dtype=np.float64)[:2, :2]
        first_norm = float(np.linalg.norm(linear[:, 0]))
        second_norm = float(np.linalg.norm(linear[:, 1]))
        orthogonality = float(abs(np.dot(linear[:, 0], linear[:, 1])))
        determinant = float(np.linalg.det(linear))
        if (
            abs(first_norm - 1.0) > 5e-3
            or abs(second_norm - 1.0) > 5e-3
            or orthogonality > 5e-3
            or abs(determinant - 1.0) > 5e-3
        ):
            rigid_failures.append(int(fragment.index))

    grouped: dict[str, list[Any]] = {}
    for source_key, fragment in zip(original_source_keys, fragments):
        grouped.setdefault(source_key, []).append(fragment)
    source_partition_failures: list[dict[str, Any]] = []
    for source_key, items in grouped.items():
        source_shape = items[0].diagnostics.get("source_shape")
        expected_values = {
            int(item.diagnostics.get("sourceForegroundAreaPx", item.mask_area))
            for item in items
        }
        if not source_shape or len(expected_values) != 1:
            source_partition_failures.append(
                {
                    "sourcePath": source_key,
                    "reason": "missing_or_inconsistent_source_partition_metadata",
                    "expectedAreaCandidates": sorted(expected_values),
                }
            )
            continue
        source_h, source_w = [int(value) for value in source_shape]
        occupancy = np.zeros((source_h, source_w), dtype=np.uint16)
        clipped = False
        for item in items:
            x, y, _, _ = [int(value) for value in item.crop_bbox_xywh]
            height, width = item.mask.shape
            x0, y0 = max(0, x), max(0, y)
            x1, y1 = min(source_w, x + width), min(source_h, y + height)
            if x0 != x or y0 != y or x1 != x + width or y1 != y + height:
                clipped = True
            if x1 <= x0 or y1 <= y0:
                continue
            mx0, my0 = x0 - x, y0 - y
            mx1, my1 = mx0 + (x1 - x0), my0 + (y1 - y0)
            occupancy[y0:y1, x0:x1] += (
                item.mask[my0:my1, mx0:mx1] > 0
            ).astype(np.uint16)
        expected_area = next(iter(expected_values))
        preserved_area = int(np.count_nonzero(occupancy > 0))
        overlap_area = int(np.count_nonzero(occupancy > 1))
        if clipped or preserved_area != expected_area or overlap_area > 0:
            source_partition_failures.append(
                {
                    "sourcePath": source_key,
                    "reason": "source_foreground_partition_not_exact",
                    "expectedForegroundAreaPx": expected_area,
                    "preservedForegroundAreaPx": preserved_area,
                    "overlapForegroundAreaPx": overlap_area,
                    "clippedBySourceBounds": clipped,
                    "placementUnitCount": len(items),
                }
            )

    source_partition_counts = {key: len(items) for key, items in grouped.items()}
    validation = {
        "inputCount": len(fragments),
        "layoutItemCount": len(final_poses),
        "uniquePlacementUnitCount": unique_placement_count,
        "duplicatePlacementUnitCount": len(placement_keys) - unique_placement_count,
        "uniqueOriginalSourceCount": len(grouped),
        "splitOriginalSourceCount": sum(
            count > 1 for count in source_partition_counts.values()
        ),
        "maxPlacementUnitsPerOriginalSource": max(
            source_partition_counts.values(), default=0
        ),
        "missingOrExtraLayoutItemCount": abs(len(fragments) - len(final_poses)),
        "allLayoutScalesOne": True,
        "rigidTransformFailureIndices": rigid_failures,
        "sourceForegroundPartitionFailureCount": len(source_partition_failures),
        "sourceForegroundPartitionFailures": source_partition_failures,
        "passed": bool(
            len(fragments) == len(final_poses)
            and unique_placement_count == len(fragments)
            and not rigid_failures
            and not source_partition_failures
        ),
    }
    if not validation["passed"]:
        raise RuntimeError(f"출력 불변조건 검증 실패: {validation}")
    return validation

def _pair_overlay(first, second, transform: np.ndarray) -> np.ndarray:
    first_h, first_w = first.mask.shape
    second_h, second_w = second.mask.shape
    corners_first = np.array([[0, 0, 1], [first_w, 0, 1], [first_w, first_h, 1], [0, first_h, 1]], dtype=np.float64).T
    corners_second = transform @ np.array(
        [[0, 0, 1], [second_w, 0, 1], [second_w, second_h, 1], [0, second_h, 1]],
        dtype=np.float64,
    ).T
    points = np.hstack([corners_first[:2], corners_second[:2]])
    minimum = np.floor(points.min(axis=1) - 8)
    maximum = np.ceil(points.max(axis=1) + 8)
    width, height = np.maximum((maximum - minimum).astype(int), 1)
    offset = np.array([[1, 0, -minimum[0]], [0, 1, -minimum[1]], [0, 0, 1]], dtype=np.float64)
    first_mask = cv2.warpAffine(first.mask, offset[:2].astype(np.float32), (int(width), int(height)), flags=cv2.INTER_NEAREST)
    second_mask = cv2.warpAffine(second.mask, (offset @ transform)[:2].astype(np.float32), (int(width), int(height)), flags=cv2.INTER_NEAREST)
    overlay = np.zeros((int(height), int(width), 3), dtype=np.uint8)
    overlay[first_mask > 0] = (40, 190, 40)
    overlay[second_mask > 0] = (190, 40, 190)
    overlay[(first_mask > 0) & (second_mask > 0)] = (40, 220, 220)
    return overlay


def run_assembly(
    reference_path: str | Path,
    fragments_dir: str | Path,
    output_dir: str | Path,
    config: AssemblyConfig,
    reference_mask_path: str | Path | None = None,
    fragment_masks_dir: str | Path | None = None,
    route_request: RouteRequest | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    debug_root = output_root / "debug"
    debug_root.mkdir(parents=True, exist_ok=True)

    reference_path = Path(reference_path)
    reference_image = read_image(reference_path)
    reference_mask, reference_diagnostics = segment_reference(
        reference_image,
        config,
        explicit_mask_path=reference_mask_path,
    )
    _write_image(debug_root / "01_reference_mask_original.png", reference_mask)

    fragment_paths = list_images(fragments_dir)
    fragments = load_fragments(fragment_paths, config, masks_dir=fragment_masks_dir)
    masks_debug_dir = debug_root / "fragment_masks"
    masks_debug_dir.mkdir(exist_ok=True)
    for fragment in fragments:
        _write_image(masks_debug_dir / f"{Path(fragment.name).stem}_mask.png", fragment.mask)

    route = resolve_route(reference_mask, fragments, config, route_request)
    if route.algorithm_route == "fragment_array":
        fragments = split_multi_object_fragments(fragments, config)

    # Konva 최종 확정 후 동일한 조각 마스크로 다시 렌더링할 수 있도록
    # 실제 layout에 들어가는 최종 fragment(분할 subfragment 포함)의 마스크를
    # fragment index 기준으로 보존한다. 기존 fragment_masks는 원본 segmentation
    # audit trail로 그대로 둔다.
    layout_masks_debug_dir = debug_root / "layout_fragment_masks"
    layout_masks_debug_dir.mkdir(exist_ok=True)
    for fragment in fragments:
        _write_image(
            layout_masks_debug_dir / f"fragment_{int(fragment.index):04d}.png",
            fragment.mask,
        )

    if route.algorithm_route == "fragment_array":
        result = stitch_fragment_array(
            fragments, reference_image, reference_mask, config, route
        )
    elif route.algorithm_route == "mixed_reference":
        result = stitch_mixed_reference(
            fragments, reference_image, reference_mask, config, route
        )
    else:
        result = stitch_fragments(fragments, reference_image, reference_mask, config)
    geometry = result["geometry"]
    placement_mode = str(result.get("placementMode", ""))
    fragment_array_reference_matching = bool(
        route.algorithm_route == "fragment_array"
        and placement_mode in {
            "color_fragment_array_reference_matching",
            "reference_anchored_partial_source_frames",
        }
    )
    _write_image(debug_root / "02_reference_mask_scaled.png", geometry.target_mask_full)
    _write_image(debug_root / "03_reference_preview_scaled.jpg", geometry.target_preview_full)
    _write_image(debug_root / "04_xray_mosaic_before_color_alignment.png", result["initialMosaic"])
    _write_image(debug_root / "05_xray_mosaic_mask_before_color_alignment.png", result["initialMask"])
    _write_image(debug_root / "06_overlap_count.png", np.clip(result["finalCounts"], 0, 255).astype(np.uint8))

    pair_debug_dir = debug_root / "selected_pair_registrations"
    pair_debug_dir.mkdir(exist_ok=True)
    for edge in result["treeEdges"]:
        overlay = _pair_overlay(
            fragments[edge.first_index],
            fragments[edge.second_index],
            edge.second_to_first,
        )
        _write_image(
            pair_debug_dir / f"{edge.first_index:03d}_{edge.second_index:03d}_{edge.method}.png",
            overlay,
        )

    assembled = result["finalMosaic"]
    assembly_mask = result["finalMask"]
    if route.algorithm_route == "fragment_array" and result.get("diagnosticOverlay") is not None:
        overlay = result["diagnosticOverlay"]
    else:
        overlay = make_alignment_overlay(
            geometry.target_preview_full,
            geometry.target_mask_full,
            assembly_mask,
            result["finalCounts"],
        )
    _write_image(output_root / "assembled_xray.png", assembled)
    _write_image(output_root / "assembly_mask.png", assembly_mask)
    _write_image(output_root / "reference_vs_assembly_overlay.png", overlay)
    if route.algorithm_route == "fragment_array":
        overlay_name = (
            "fragment_array_reference_matching_overlay.png"
            if fragment_array_reference_matching
            else "neutral_component_packing_overlay.png"
        )
        _write_image(output_root / overlay_name, overlay)
        reference_slot_search_overlay = result.get("referenceSlotSearchOverlay")
        if reference_slot_search_overlay is not None:
            _write_image(
                output_root / "fragment_array_reference_slot_search_polygons.png",
                reference_slot_search_overlay,
            )
            _write_image(
                debug_root / "07_reference_slot_search_polygons.png",
                reference_slot_search_overlay,
            )
    elif route.algorithm_route == "mixed_reference":
        _write_image(output_root / "body_first_mixed_overlay.png", overlay)

    pairwise_json = {
        "routing": route.to_dict(),
        "placementMode": placement_mode or None,
        "registrationMode": result.get("registrationDebug", {}).get("registrationMode"),
        "captureRolePolicy": result.get("registrationDebug", {}).get("captureRolePolicy"),
        "partialCaptureCandidateIndices": result.get("registrationDebug", {}).get(
            "partialCaptureCandidateIndices", []
        ),
        "independentFragmentCandidateIndices": result.get("registrationDebug", {}).get(
            "independentFragmentCandidateIndices", []
        ),
        "longestScannerRun": result.get("registrationDebug", {}).get("longestScannerRun", 0),
        "allRegistrations": [registration.to_dict() for registration in result["registrations"]],
        "selectedTreeEdges": [registration.to_dict() for registration in result["treeEdges"]],
        "components": result["components"],
        "candidateAlternatives": result.get("registrationDebug", {}).get("candidateAlternatives", {}),
        "sequenceResolution": result.get("registrationDebug", {}).get("sequenceResolution", []),
        "sequenceBreaks": result.get("registrationDebug", {}).get("sequenceBreaks", []),
        "extendedSiftAnchors": result.get("registrationDebug", {}).get("extendedSiftAnchors", []),
        "cycleConsistency": result.get("registrationDebug", {}).get("cycleConsistency", []),
        "forestSelection": result.get("registrationDebug", {}).get("forestSelection", {}),
        "poseGraph": result.get("registrationDebug", {}).get("poseGraph", []),
        "sourceConsensus": result.get("registrationDebug", {}).get(
            "sourceConsensus", []
        ),
        "attemptedPairCount": result.get("registrationDebug", {}).get(
            "attemptedPairCount", 0
        ),
        "candidatePairDiagnostics": result.get("registrationDebug", {}).get(
            "candidatePairDiagnostics", []
        ),
        "strictRegistrationCount": result.get("registrationDebug", {}).get(
            "strictRegistrationCount", 0
        ),
        "siftAcceptedCount": result.get("registrationDebug", {}).get(
            "siftAcceptedCount", 0
        ),
        "phaseAcceptedCount": result.get("registrationDebug", {}).get(
            "phaseAcceptedCount", 0
        ),
        "rejectedPairCount": result.get("registrationDebug", {}).get(
            "rejectedPairCount", 0
        ),
        "sourceConsensusAcceptedEdgeCount": result.get(
            "registrationDebug", {}
        ).get("sourceConsensusAcceptedEdgeCount", 0),
        "sourceConsensusAcceptedFramePairCount": result.get(
            "registrationDebug", {}
        ).get("sourceConsensusAcceptedFramePairCount", 0),
        "resultComponentCount": result.get("registrationDebug", {}).get(
            "resultComponentCount", len(result["components"])
        ),
        "multiSourceComponentCount": result.get("registrationDebug", {}).get(
            "multiSourceComponentCount", 0
        ),
        "partialCaptureComponentCount": result.get(
            "registrationDebug", {}
        ).get("partialCaptureComponentCount", 0),
        "unconsolidatedPartialCaptureComponentCount": result.get(
            "registrationDebug", {}
        ).get("unconsolidatedPartialCaptureComponentCount", 0),
        "componentSourceImageCounts": result.get("registrationDebug", {}).get(
            "componentSourceImageCounts", []
        ),
        "phaseEnabled": result.get("registrationDebug", {}).get("phaseEnabled"),
        "phaseSkippedBecauseFragmentCountExceeds": result.get(
            "registrationDebug", {}
        ).get("phaseSkippedBecauseFragmentCountExceeds"),
        "mixedBodyPreclassification": result.get("registrationDebug", {}).get(
            "mixedBodyPreclassification"
        ),
        "mixedRegistrationRuns": result.get("registrationDebug", {}).get(
            "mixedRegistrationRuns", []
        ),
        "excludedFromInitialRegistration": result.get("registrationDebug", {}).get(
            "excludedFromInitialRegistration", []
        ),
        "sourceFramePlacements": result.get("registrationDebug", {}).get(
            "sourceFramePlacements", []
        ),
        "nToOneReferenceSlotReuseEnabled": result.get(
            "registrationDebug", {}
        ).get("nToOneReferenceSlotReuseEnabled", False),
        "unassignedParkingMode": result.get("registrationDebug", {}).get(
            "unassignedParkingMode"
        ),
        "renderingPolicy": result.get("registrationDebug", {}).get(
            "renderingPolicy"
        ),
    }
    save_json(output_root / "pairwise_registration.json", pairwise_json)

    placements = result["placements"]
    invariant_validation = _validate_transform_and_source_invariants(
        fragments, result["finalPoses"]
    )
    fragment_assignment_metadata: dict[int, dict[str, Any]] = {}
    for component_report in result.get("alignmentScore", {}).get("components", []):
        status = str(component_report.get("status", "assigned_to_reference_component"))
        metadata = {
            "xrayComponentIndex": component_report.get("xrayComponentIndex"),
            "sourceFrameIndex": component_report.get("sourceFrameIndex"),
            "sourceFile": component_report.get("sourceFile"),
            "sourceFramePath": component_report.get("sourcePath"),
            "sourceFrameStatus": component_report.get("status"),
            "sourceFrameSupportedFragmentCount": component_report.get(
                "supportedFragmentCount"
            ),
            "sourceFrameRequiredSupportedFragmentCount": component_report.get(
                "requiredSupportedFragmentCount"
            ),
            "assignmentStatus": status,
            "referenceComponentIndex": component_report.get("referenceComponentIndex"),
            "referenceSlotIndices": component_report.get("referenceSlotIndices", []),
            "assignmentReviewRequired": bool(component_report.get("reviewRequired", False)),
            "componentRole": component_report.get("componentRole"),
            "componentCaptureRoles": component_report.get("captureRoles", []),
            "referenceRole": component_report.get("referenceRole"),
            "packingOrder": component_report.get("packingOrder"),
            "packingBBoxXYWH": component_report.get("packingBBoxXYWH"),
            "componentSourceImageCount": component_report.get("sourceImageCount"),
            "assignmentMatchingMode": component_report.get("matchingMode"),
            "assignmentUnassignedReason": component_report.get("unassignedReason"),
            "assignmentUnassignedReasonDetails": component_report.get(
                "unassignedReasonDetails", []
            ),
            "assignmentCandidateGeneration": component_report.get(
                "candidateGeneration"
            ),
            "assignmentPrimaryReferenceSlotIndex": component_report.get(
                "primaryReferenceSlotIndex"
            ),
            "assignmentCombinedMatchScore": component_report.get(
                "combinedMatchScore"
            ),
            "assignmentMutualBest": component_report.get("mutualBest"),
            "assignmentBundleScoreMargin": component_report.get(
                "bundleScoreMargin"
            ),
            "assignmentSlotScoreMargin": component_report.get("slotScoreMargin"),
        }
        for fragment_index in component_report.get("fragmentIndices", []):
            fragment_assignment_metadata[int(fragment_index)] = metadata

    layout = {
        "schemaVersion": "2.1",
        "algorithm": (
            (
                (
                    "xray_source_frames_direct_to_color_reference_partial_n_to_one"
                    if placement_mode == "reference_anchored_partial_source_frames"
                    else "xray_border_classification_then_overlap_consolidation_then_color_fragment_array_matching"
                )
                if fragment_array_reference_matching
                else "xray_strong_overlap_then_neutral_component_packing"
            )
            if route.algorithm_route == "fragment_array"
            else (
                "xray_overlap_registration_then_body_first_detached_component_assignment"
                if route.algorithm_route == "mixed_reference"
                else "xray_overlap_registration_then_color_global_alignment"
            )
        ),
        "routing": route.to_dict(),
        "coordinateSystem": "output_canvas_pixels",
        "canvas": {
            "width": geometry.canvas_width_full,
            "height": geometry.canvas_height_full,
            "backgroundValue": 0,
        },
        "constraints": {
            "fragmentScale": 1.0,
            "allowedTransforms": ["rotation", "translation"],
            "nonlinearWarp": False,
            "perspectiveWarp": False,
            "generativeCompletion": False,
            "blending": False,
            "interpolation": "nearest",
            "eachFragmentUsedOnce": True,
            "runtimeInvariantValidation": invariant_validation,
        },
        "reference": {
            "path": str(reference_path),
            "role": (
                (
                    (
                        "source_frame_direct_position_and_rotation_reference_only"
                        if placement_mode == "reference_anchored_partial_source_frames"
                        else "fragment_array_component_position_and_rotation_reference_only"
                    )
                    if fragment_array_reference_matching
                    else "fragment_array_routing_only_not_used_for_position_or_order"
                )
                if route.algorithm_route == "fragment_array"
                else (
                    "primary_body_and_detached_fragment_component_assignment_reference_only"
                    if route.algorithm_route == "mixed_reference"
                    else "stitched_mosaic_global_orientation_and_silhouette_only"
                )
            ),
            "scaleAppliedToReferenceOnly": geometry.reference_scale,
            "segmentation": reference_diagnostics,
        },
        "registration": {
            "connectedComponents": result["components"],
            "selectedPairCount": len(result["treeEdges"]),
            "globalTransform": result["globalTransform"].tolist(),
            "globalAlignment": result["alignmentScore"],
        },
        "fragments": [
            {
                "index": fragment.index,
                "file": fragment.name,
                "sourcePath": str(fragment.path),
                "originalSourcePath": str(fragment.source_path or fragment.path),
                "originalSourceName": fragment.source_name or fragment.name,
                "originalSourceIndex": fragment.source_index if fragment.source_index is not None else fragment.index,
                "subfragmentIndex": int(getattr(fragment, "subfragment_index", 0)),
                "centerX": placement.center_x,
                "centerY": placement.center_y,
                "rotationDeg": placement.rotation_deg,
                "scale": 1.0,
                "affineMatrix": result["finalPoses"][fragment.index].tolist(),
                "originalCropBBoxXYWH": list(fragment.crop_bbox_xywh),
                "maskAreaPx": fragment.mask_area,
                "maskDiagnostics": fragment.diagnostics,
                **fragment_assignment_metadata.get(fragment.index, {}),
            }
            for fragment, placement in zip(fragments, placements)
        ],
    }
    save_json(output_root / "layout.json", layout)

    elapsed = time.perf_counter() - started
    target = geometry.target_mask_full > 0
    union = assembly_mask > 0
    intersection = int(np.count_nonzero(target & union))
    union_area = int(np.count_nonzero(target | union))
    final_iou = (
        intersection / max(union_area, 1)
        if route.algorithm_route != "fragment_array" or fragment_array_reference_matching
        else None
    )
    total_layer_pixels = int(result["finalCounts"].sum())
    overlap_pixels = int(np.maximum(result["finalCounts"].astype(np.int32) - 1, 0).sum())
    evaluate_against_reference = bool(
        route.algorithm_route != "fragment_array" or fragment_array_reference_matching
    )
    outside_pixels = (
        int(result["finalCounts"][~target].sum())
        if evaluate_against_reference
        else 0
    )
    missing_pixels = (
        int(np.count_nonzero(target & ~union))
        if evaluate_against_reference
        else 0
    )
    overlap_ratio = overlap_pixels / max(total_layer_pixels, 1)
    outside_ratio = outside_pixels / max(total_layer_pixels, 1)
    missing_ratio = missing_pixels / max(int(np.count_nonzero(target)), 1)

    component_alignment = result["alignmentScore"]
    unresolved_components = max(
        0,
        int(component_alignment.get("xrayComponentCount", len(result["components"])))
        - int(component_alignment.get("assignedComponentCount", 0)),
    )
    review_required_edges = [
        edge for edge in result["treeEdges"] if edge.review_required
    ]
    forest_selection = result.get("registrationDebug", {}).get(
        "forestSelection", {}
    )
    weak_selected_dominates = bool(
        forest_selection.get("mode") == "image_supported_priority"
        and forest_selection.get("weakSelectedDominates", False)
    )
    pose_graph_diagnostics = result.get("registrationDebug", {}).get(
        "poseGraph", []
    )
    pose_graph_insufficient_evidence = any(
        item.get("reason") == "insufficient_image_supported_pose_graph_coverage"
        for item in pose_graph_diagnostics
    )
    component_assignment_needs_review = any(
        bool(item.get("reviewRequired", False))
        for item in component_alignment.get("components", [])
    )
    fragment_registration_needs_review = bool(
        unresolved_components > 0
        or review_required_edges
        or weak_selected_dominates
        or pose_graph_insufficient_evidence
        or component_assignment_needs_review
        or route.review_required
    )
    boundary_value = result["alignmentScore"].get("boundaryF1")
    boundary_f1 = float(boundary_value) if boundary_value is not None else 0.0
    if route.algorithm_route == "fragment_array":
        if fragment_array_reference_matching:
            array_metrics = component_alignment.get("arrayMetrics", {})
            global_alignment_needs_review = bool(
                float(array_metrics.get("assemblyInsideReferenceRatio", 0.0))
                < float(config.fragment_array_review_inside_ratio)
                or int(array_metrics.get("unassignedXrayComponentCount", 0)) > 0
                or int(array_metrics.get("ambiguousXrayComponentCount", 0)) > 0
            )
        else:
            neutral_metrics = component_alignment.get("neutralPackingMetrics", {})
            global_alignment_needs_review = bool(
                int(neutral_metrics.get("interComponentOverlapPixels", 0)) > 0
                or int(neutral_metrics.get("unassignedXrayComponentCount", 0)) > 0
                or int(neutral_metrics.get("ambiguousConsolidatedComponentCount", 0)) > 0
            )
    elif route.algorithm_route == "mixed_reference":
        mixed_metrics = component_alignment.get("mixedMetrics", {})
        global_alignment_needs_review = bool(
            float(mixed_metrics.get("bodyInsideReferenceRatio", 0.0))
            < float(config.mixed_review_inside_ratio)
            or int(mixed_metrics.get("unassignedXrayComponentCount", 0)) > 0
            or int(mixed_metrics.get("ambiguousXrayComponentCount", 0)) > 0
        )
    else:
        global_alignment_needs_review = bool(
            float(final_iou) < float(config.review_min_global_iou)
            or boundary_f1 < float(config.review_min_boundary_f1)
        )
    reference_segmentation_needs_review = bool(
        reference_diagnostics.get("quality", 0.0) < -0.8
    )
    initial_placement_needs_review = bool(
        reference_segmentation_needs_review
        or fragment_registration_needs_review
        or global_alignment_needs_review
    )
    report = {
        "status": "completed",
        "elapsedSeconds": elapsed,
        "fragmentCount": len(fragments),
        "originalSourceCount": len({str(fragment.source_path or fragment.path) for fragment in fragments}),
        "splitPlacementUnitCount": sum(1 for fragment in fragments if int(getattr(fragment, "subfragment_index", 0)) > 0 or bool(fragment.diagnostics.get("splitFromMultiObjectSource", False))),
        "physicalFragmentObservationCount": result["alignmentScore"].get(
            "neutralPackingMetrics", {}
        ).get("physicalFragmentObservationCount"),
        "physicalFragmentComponentCount": result["alignmentScore"].get(
            "neutralPackingMetrics", {}
        ).get("physicalFragmentComponentCount"),
        "residualPlacementUnitCount": result["alignmentScore"].get(
            "neutralPackingMetrics", {}
        ).get("residualPlacementUnitCount"),
        "referenceScaleAppliedToReferenceOnly": geometry.reference_scale,
        "outputCanvas": [geometry.canvas_width_full, geometry.canvas_height_full],
        "routing": route.to_dict(),
        "algorithm": {
            "name": (
                (
                    (
                        "Original X-ray source-frame direct partial alignment to color reference"
                        if placement_mode == "reference_anchored_partial_source_frames"
                        else "Border-guided X-ray overlap consolidation + color fragment-array matching"
                    )
                    if fragment_array_reference_matching
                    else "Strong X-ray overlap consolidation + neutral component packing"
                )
                if route.algorithm_route == "fragment_array"
                else (
                    "X-ray overlap registration + primary-body lock + detached-component assignment"
                    if route.algorithm_route == "mixed_reference"
                    else "X-ray overlap registration + pose forest + color global alignment"
                )
            ),
            "pairRegistration": (
                [
                    "source-frame constellation mask correlation",
                    "rigid rotation/translation refinement",
                ]
                if placement_mode == "reference_anchored_partial_source_frames"
                else ["SIFT/RANSAC fixed-scale", "rotation-search phase correlation"]
            ),
            "registrationPolicy": (
                (
                    (
                        "no_cross_source_stitching_source_frame_constellation_partial_n_to_one"
                        if placement_mode == "reference_anchored_partial_source_frames"
                        else "border_touch_candidate_pairing_then_direct_image_evidence_then_color_slot_matching"
                    )
                    if fragment_array_reference_matching
                    else "direct_image_evidence_only_then_nonsemantic_neutral_packing"
                )
                if route.algorithm_route == "fragment_array"
                else (
                    "primary_body_first_detached_slots_no_force_assignment"
                    if route.algorithm_route == "mixed_reference"
                    else "validated_complete_reference_policy"
                )
            ),
            "rendering": (
                "first original pixel wins, no blending, uint8 overlap counts"
                if placement_mode == "reference_anchored_partial_source_frames"
                else "distance-to-mask-edge winner, no blending"
            ),
        },
        "scores": {
            "globalAlignment": {
                **result["alignmentScore"],
                "outside_ratio": outside_ratio,
                "overlap_ratio": overlap_ratio,
                "missing_ratio": missing_ratio,
            },
            # Compatibility for the existing batch summary reader.
            "refined": {
                "iou": final_iou,
                "outside_ratio": outside_ratio,
                "overlap_ratio": overlap_ratio,
                "missing_ratio": missing_ratio,
                "boundary_f1": result["alignmentScore"].get("boundaryF1"),
            },
            "finalFullResolutionIoU": final_iou,
        },
        "registration": {
            "mode": result.get("registrationDebug", {}).get("registrationMode"),
            "placementMode": placement_mode or None,
            "captureRolePolicy": result.get("registrationDebug", {}).get(
                "captureRolePolicy"
            ),
            "partialCaptureCandidateIndices": result.get(
                "registrationDebug", {}
            ).get("partialCaptureCandidateIndices", []),
            "independentFragmentCandidateIndices": result.get(
                "registrationDebug", {}
            ).get("independentFragmentCandidateIndices", []),
            "longestScannerRun": result.get("registrationDebug", {}).get("longestScannerRun", 0),
            "candidatePairCount": len(result["registrations"]),
            "selectedPairCount": len(result["treeEdges"]),
            "componentCount": len(result["components"]),
            "components": result["components"],
            "componentAssignments": component_alignment.get("components", []),
            "sequenceResolution": result.get("registrationDebug", {}).get("sequenceResolution", []),
            "sequenceBreaks": result.get("registrationDebug", {}).get("sequenceBreaks", []),
            "extendedSiftAnchors": result.get("registrationDebug", {}).get("extendedSiftAnchors", []),
            "cycleConsistency": result.get("registrationDebug", {}).get("cycleConsistency", []),
            "forestSelection": forest_selection,
            "poseGraph": pose_graph_diagnostics,
            "sourceConsensusAcceptedEdgeCount": result.get(
                "registrationDebug", {}
            ).get("sourceConsensusAcceptedEdgeCount", 0),
            "sourceConsensusAcceptedFramePairCount": result.get(
                "registrationDebug", {}
            ).get("sourceConsensusAcceptedFramePairCount", 0),
            "sourceConsensus": result.get("registrationDebug", {}).get(
                "sourceConsensus", []
            ),
            "reviewRequiredEdges": [
                edge.to_dict() for edge in review_required_edges
            ],
            "multiplePhysicalComponents": len(result["components"]) > 1,
            "unassignedComponentCount": unresolved_components,
            "needsHitlForUnassignedComponent": unresolved_components > 0,
            "sourceFramePlacements": result.get("registrationDebug", {}).get(
                "sourceFramePlacements", []
            ),
            "nToOneReferenceSlotReuseEnabled": result.get(
                "registrationDebug", {}
            ).get("nToOneReferenceSlotReuseEnabled", False),
            "unassignedParkingMode": result.get("registrationDebug", {}).get(
                "unassignedParkingMode"
            ),
        },
        "qualityFlags": {
            "overlapPixels": overlap_pixels,
            "hasOverlap": overlap_pixels > 0,
            "referenceSegmentationNeedsReview": reference_segmentation_needs_review,
            "fragmentRegistrationNeedsReview": fragment_registration_needs_review,
            "globalAlignmentNeedsReview": global_alignment_needs_review,
            "weakContinuationDominatesSelectedGraph": weak_selected_dominates,
            "poseGraphInsufficientImageEvidence": pose_graph_insufficient_evidence,
            "componentAssignmentNeedsReview": component_assignment_needs_review,
            "routingConflictNeedsReview": route.review_required,
            "initialPlacementNeedsReview": initial_placement_needs_review,
            "runtimeInvariantValidation": invariant_validation,
        },
        "notes": (
            (
                (
                    [
                        "서로 다른 원본 X-ray 촬영본끼리는 사전에 결합하지 않았습니다.",
                        "한 원본 촬영본에서 분리된 객체들의 상대 위치를 유지한 채 촬영 프레임 전체에 하나의 rigid rotation·translation을 적용했습니다.",
                        "여러 원본 촬영본이 동일한 컬러 actual fragmentMask 슬롯을 지지하는 N:1 관계를 허용했습니다.",
                        "컬러 crop·bounding box·Voronoi 경계는 부분 촬영 판정에 사용하지 않았습니다.",
                        "기준을 충족하지 못한 촬영본은 강제 배치하지 않고 분리 객체 단위로 하단 Konva 검토 영역에 보류했습니다.",
                        "컬러 이미지는 위치·회전 기준으로만 사용했으며 X-ray 픽셀에 섞지 않았습니다.",
                        "X-ray에는 scale, 비선형 변형, perspective warp를 적용하지 않았습니다.",
                        "중첩 구간은 블렌딩하지 않고 먼저 배치된 원본 픽셀 하나만 표시하며 중첩 횟수는 별도로 기록했습니다.",
                    ]
                    if placement_mode == "reference_anchored_partial_source_frames"
                    else [
                        "원본 X-ray 프레임에서 물체가 영상 경계에 닿으면 부분 촬영 후보, 사방이 검은 배경으로 둘러싸이면 독립 파편 후보로 분류했습니다.",
                        "두 독립 파편끼리는 X-ray 중첩 결합 후보로 만들지 않았고, 적어도 한쪽이 부분 촬영 후보인 경우에만 영상 정합을 시도했습니다.",
                        "직접 영상 중첩 근거로 결합된 component와 독립 파편을 컬러 파편 배열의 실루엣에 회전·이동으로 매칭했습니다.",
                        "기준을 충족하지 못한 component는 강제 배치하지 않고 Konva 검토 영역에 유지했습니다.",
                        "컬러 이미지는 위치·회전 기준으로만 사용했으며 X-ray 픽셀에 섞지 않았습니다.",
                        "X-ray 파편에는 scale, 비선형 변형, perspective warp를 적용하지 않았습니다.",
                        "중첩 구간은 블렌딩하지 않고 원본 픽셀 하나만 선택했습니다.",
                    ]
                )
                if fragment_array_reference_matching
                else [
                    "파편 배열 모드에서는 직접 영상 근거가 강한 X-ray 중첩만 등록했습니다.",
                    "서로 다른 registration component를 하나의 완성 실루엣으로 강제 결합하지 않았습니다.",
                    "컬러 이미지는 파편 배열형 라우팅 판정에만 사용했으며 component 위치·순서 결정에는 사용하지 않았습니다.",
                    "같은 물리 파편의 분할 촬영본은 직접 영상 중첩 근거가 강한 경우에만 내부 결합했습니다.",
                    "독립 component는 의미 없는 중립적 순서로 겹치지 않게 한 캔버스에 배치했습니다.",
                    "파편 배열 모드에서는 컬러 reference IoU를 계산하거나 성공 지표로 사용하지 않습니다.",
                    "X-ray 파편에는 scale, 비선형 변형, perspective warp를 적용하지 않았습니다.",
                    "중첩 구간은 블렌딩하지 않고 원본 픽셀 하나만 선택했습니다.",
                ]
            )
            if route.algorithm_route == "fragment_array"
            else (
                [
                    "가장 강한 본체 registration component를 가장 큰 컬러 reference component에 먼저 고정했습니다.",
                    "나머지 X-ray component는 본체를 제외한 별도 reference slot에만 배정했습니다.",
                    "기준을 충족하지 못한 component는 본체에 붙이지 않고 Konva 검토 영역에 미배정 상태로 유지했습니다.",
                    "컬러 이미지는 component 위치·회전 기준으로만 사용했으며 X-ray 픽셀에 섞지 않았습니다.",
                    "X-ray 파편에는 scale, 비선형 변형, perspective warp를 적용하지 않았습니다.",
                    "중첩 구간은 블렌딩하지 않고 원본 픽셀 하나만 선택했습니다.",
                ]
                if route.algorithm_route == "mixed_reference"
                else [
                    "X-ray 파편끼리 동일 영상 내용의 중첩을 먼저 등록했습니다.",
                    "컬러 완성본은 조립된 X-ray 전체의 전역 회전·이동과 silhouette 검증에만 사용했습니다.",
                    "X-ray 파편에는 scale, 비선형 변형, perspective warp를 적용하지 않았습니다.",
                    "중첩 구간은 블렌딩하지 않고 마스크 경계에서 더 먼 원본 픽셀 하나만 선택했습니다.",
                    "overlay에서 초록=일치, 파랑=reference만 존재, 빨강=assembly만 존재, 자홍=원본 X-ray 타일 중첩입니다.",
                ]
            )
        ),
        "config": config.to_dict(),
    }
    save_json(output_root / "report.json", report)
    return report
