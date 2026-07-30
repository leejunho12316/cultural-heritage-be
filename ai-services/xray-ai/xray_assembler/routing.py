from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from .config import AssemblyConfig
from .models import Fragment

REFERENCE_MODES = {
    "auto",
    "complete",
    "complete_with_separate_fragments",
    "fragment_array",
}
CAPTURE_MODES = {
    "auto",
    "split_scan",
    "independent_fragments",
    "mixed",
}
SHAPE_PRIORS = {
    "auto",
    "round_container",
    "elongated",
    "irregular",
}


@dataclass(frozen=True)
class RouteRequest:
    reference_mode: str = "auto"
    capture_mode: str = "auto"
    shape_prior: str = "auto"
    source: str = "default"

    def __post_init__(self) -> None:
        if self.reference_mode not in REFERENCE_MODES:
            raise ValueError(f"지원하지 않는 reference mode: {self.reference_mode}")
        if self.capture_mode not in CAPTURE_MODES:
            raise ValueError(f"지원하지 않는 capture mode: {self.capture_mode}")
        if self.shape_prior not in SHAPE_PRIORS:
            raise ValueError(f"지원하지 않는 shape prior: {self.shape_prior}")

    def to_dict(self) -> dict[str, str]:
        return {
            "referenceMode": self.reference_mode,
            "captureMode": self.capture_mode,
            "shapePrior": self.shape_prior,
            "source": self.source,
        }


@dataclass
class RouteDecision:
    algorithm_route: str
    requested: RouteRequest
    resolved_reference_mode: str
    resolved_capture_mode: str
    shape_prior: str
    reference_evidence: dict[str, Any]
    capture_evidence: dict[str, Any]
    warnings: list[dict[str, Any]] = field(default_factory=list)
    review_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithmRoute": self.algorithm_route,
            "requested": self.requested.to_dict(),
            "resolved": {
                "referenceMode": self.resolved_reference_mode,
                "captureMode": self.resolved_capture_mode,
                "shapePrior": self.shape_prior,
            },
            "referenceEvidence": self.reference_evidence,
            "captureEvidence": self.capture_evidence,
            "warnings": self.warnings,
            "reviewRequired": bool(self.review_required),
        }


def _component_summary(mask: np.ndarray, minimum_area_ratio: float) -> dict[str, Any]:
    binary = (mask > 0).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if count <= 1:
        return {
            "componentCount": 0,
            "areas": [],
            "largestAreaFraction": 0.0,
            "totalAreaPx": 0,
        }
    raw_areas = [int(value) for value in stats[1:, cv2.CC_STAT_AREA]]
    largest = max(raw_areas)
    threshold = max(12, int(round(largest * minimum_area_ratio)))
    areas = sorted((area for area in raw_areas if area >= threshold), reverse=True)
    total = sum(areas)
    return {
        "componentCount": len(areas),
        "areas": areas,
        "largestAreaFraction": float(areas[0] / total) if total else 0.0,
        "totalAreaPx": int(total),
        "minimumAreaPx": int(threshold),
    }


def _detect_reference_mode(reference_mask: np.ndarray, config: AssemblyConfig) -> tuple[str, float, dict[str, Any]]:
    summary = _component_summary(reference_mask, config.min_component_area_ratio)
    component_count = int(summary["componentCount"])
    largest_fraction = float(summary["largestAreaFraction"])

    if (
        component_count >= int(config.route_fragment_array_min_components)
        and largest_fraction <= float(config.route_fragment_array_max_largest_fraction)
    ):
        excess = min(
            1.0,
            (component_count - int(config.route_fragment_array_min_components) + 1)
            / max(float(config.route_fragment_array_min_components), 1.0),
        )
        separation = min(
            1.0,
            max(
                0.0,
                (float(config.route_fragment_array_max_largest_fraction) - largest_fraction)
                / max(float(config.route_fragment_array_max_largest_fraction), 1e-6),
            ),
        )
        confidence = float(min(0.99, 0.88 + 0.06 * excess + 0.05 * separation))
        detected = "fragment_array"
        reason = "many_balanced_reference_components"
    elif component_count <= 1:
        detected = "complete"
        confidence = 0.96 if component_count == 1 else 0.60
        reason = "single_reference_component" if component_count == 1 else "no_reference_component"
    else:
        detected = "complete_with_separate_fragments"
        if largest_fraction >= 0.55:
            confidence = min(0.94, 0.72 + 0.35 * (largest_fraction - 0.55))
            reason = "dominant_body_with_detached_components"
        else:
            confidence = 0.58
            reason = "several_reference_components_without_array_level_evidence"

    summary.update(
        {
            "detectedMode": detected,
            "confidence": float(confidence),
            "reason": reason,
        }
    )
    return detected, float(confidence), summary


def _mask_component_count(mask: np.ndarray) -> int:
    count, _, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
    if count <= 1:
        return 0
    areas = [int(value) for value in stats[1:, cv2.CC_STAT_AREA]]
    largest = max(areas)
    return sum(area >= max(8, int(round(largest * 0.002))) for area in areas)


def _series_key(fragment: Fragment) -> str:
    stem = fragment.path.stem.strip()
    match = re.match(r"^(.*?)-\s*(\d+)$", stem)
    if match is None:
        return stem.casefold()
    return re.sub(r"\s+", " ", match.group(1).strip()).casefold()


def _detect_capture_mode(fragments: list[Fragment]) -> tuple[str, float, dict[str, Any]]:
    fragment_count = len(fragments)
    if fragment_count == 0:
        return "auto", 0.0, {"detectedMode": "auto", "confidence": 0.0}
    touches = [bool(fragment.diagnostics.get("touches_frame", False)) for fragment in fragments]
    component_counts = [_mask_component_count(fragment.mask) for fragment in fragments]
    scanner_ratio = float(sum(touches) / fragment_count)
    multi_object_ratio = float(sum(count > 1 for count in component_counts) / fragment_count)
    series_count = len({_series_key(fragment) for fragment in fragments})

    if scanner_ratio >= 0.65 and fragment_count >= 3:
        single_object_ratio = 1.0 - multi_object_ratio
        if multi_object_ratio >= 0.20 and single_object_ratio >= 0.20:
            detected = "mixed"
            confidence = min(0.90, 0.70 + 0.20 * scanner_ratio)
            reason = "frame_clipped_tiles_mix_single_and_multi_object_frames"
        else:
            detected = "split_scan"
            confidence = min(0.92, 0.72 + 0.20 * scanner_ratio)
            reason = "mostly_frame_clipped_scanner_tiles"
    elif scanner_ratio <= 0.20 and multi_object_ratio <= 0.10:
        detected = "independent_fragments"
        confidence = 0.82
        reason = "mostly_unclipped_single_object_frames"
    else:
        detected = "mixed"
        confidence = 0.62
        reason = "mixed_frame_and_object_evidence"

    evidence = {
        "detectedMode": detected,
        "confidence": float(confidence),
        "reason": reason,
        "fragmentCount": fragment_count,
        "scannerFrameRatio": scanner_ratio,
        "multiObjectFrameRatio": multi_object_ratio,
        "maskComponentCounts": component_counts,
        "seriesCount": series_count,
    }
    return detected, float(confidence), evidence


def _resolve_requested_mode(
    requested: str,
    detected: str,
    confidence: float,
    field_name: str,
    config: AssemblyConfig,
    warnings: list[dict[str, Any]],
) -> str:
    if requested == "auto" or requested == detected:
        return detected
    if confidence >= float(config.route_image_override_confidence):
        warnings.append(
            {
                "code": f"{field_name}_request_overridden_by_image_evidence",
                "requested": requested,
                "detected": detected,
                "confidence": float(confidence),
            }
        )
        return detected
    warnings.append(
        {
            "code": f"{field_name}_request_conflicts_with_image_evidence",
            "requested": requested,
            "detected": detected,
            "confidence": float(confidence),
        }
    )
    return requested


def resolve_route(
    reference_mask: np.ndarray,
    fragments: list[Fragment],
    config: AssemblyConfig,
    request: RouteRequest | None = None,
) -> RouteDecision:
    requested = request or RouteRequest()
    detected_reference, reference_confidence, reference_evidence = _detect_reference_mode(
        reference_mask, config
    )
    detected_capture, capture_confidence, capture_evidence = _detect_capture_mode(fragments)
    warnings: list[dict[str, Any]] = []
    resolved_reference = _resolve_requested_mode(
        requested.reference_mode,
        detected_reference,
        reference_confidence,
        "reference_mode",
        config,
        warnings,
    )
    resolved_capture = _resolve_requested_mode(
        requested.capture_mode,
        detected_capture,
        capture_confidence,
        "capture_mode",
        config,
        warnings,
    )

    if resolved_reference == "fragment_array":
        algorithm_route = "fragment_array"
    elif (
        resolved_reference == "complete_with_separate_fragments"
        and requested.reference_mode == "complete_with_separate_fragments"
    ):
        # Mixed mode is opt-in through semantic routing metadata. Auto-detected
        # multi-component references stay on the established complete path so
        # existing successful artifacts do not silently change algorithms.
        algorithm_route = "mixed_reference"
    else:
        algorithm_route = "complete_reference"
    return RouteDecision(
        algorithm_route=algorithm_route,
        requested=requested,
        resolved_reference_mode=resolved_reference,
        resolved_capture_mode=resolved_capture,
        shape_prior=requested.shape_prior,
        reference_evidence=reference_evidence,
        capture_evidence=capture_evidence,
        warnings=warnings,
        review_required=bool(warnings),
    )
