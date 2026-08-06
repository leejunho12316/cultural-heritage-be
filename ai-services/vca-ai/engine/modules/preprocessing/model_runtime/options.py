"""CLI option parsing for model-backed preprocessing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from modules.preprocessing.detection.merge import (
    DetectionMergeOptions,
    DetectionMergeStrategy,
)
from modules.preprocessing.detection.scale_marker import ScaleRemovalOptions
from modules.shared import ContractValidationError

if TYPE_CHECKING:
    from collections.abc import Sequence


VALUE_OPTIONS: Final = frozenset(
    {
        "--model-cache-root",
        "--max-images",
        "--max-detections",
        "--score-threshold",
        "--detector-input-size",
        "--foreground-white-threshold",
        "--merge-strategy",
        "--merge-edge-gap",
        "--merge-min-orthogonal-overlap-ratio",
        "--scale-marker-coverage-ratio",
        "--nms-iou-threshold",
        "--nms-containment-threshold",
        "--scale-removal-padding-ratio",
    }
)


@dataclass(frozen=True, slots=True)
class RealPreprocessingOptions:
    """Validated runtime options for heavyweight preprocessing models."""

    model_cache_root: Path
    max_images: int | None
    max_detections: int | None
    detector_input_size: int | None
    foreground_white_threshold: int
    score_threshold: float
    detection_merge: DetectionMergeOptions
    scale_removal: ScaleRemovalOptions


def _option_value(arguments: Sequence[str], index: int, option: str) -> tuple[str, int]:
    next_index = index + 1
    if next_index >= len(arguments) or arguments[next_index].startswith("--"):
        raise ContractValidationError(option, "requires a value")
    return arguments[next_index], next_index


def _merge_strategy(raw: str) -> DetectionMergeStrategy:
    try:
        return DetectionMergeStrategy(raw)
    except ValueError as error:
        field = "--merge-strategy"
        reason = "must be one of: union, nms"
        raise ContractValidationError(field, reason) from error


def _max_images(raw: str) -> int | None:
    if raw == "all":
        return None
    return int(raw)


def split_real_options(
    arguments: Sequence[str],
) -> tuple[tuple[str, ...], RealPreprocessingOptions]:
    """Separate real-model options from the existing preprocessing CLI options."""
    passthrough: list[str] = []
    raw_options: dict[str, str] = {}
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in VALUE_OPTIONS:
            raw_options[argument], index = _option_value(arguments, index, argument)
        else:
            passthrough.append(argument)
        index += 1
    max_detections_raw = raw_options.get("--max-detections")
    options = RealPreprocessingOptions(
        model_cache_root=Path(raw_options.get("--model-cache-root", "models")),
        max_images=_max_images(raw_options.get("--max-images", "1")),
        max_detections=None if max_detections_raw is None else int(max_detections_raw),
        detector_input_size=None
        if "--detector-input-size" not in raw_options
        else int(raw_options["--detector-input-size"]),
        foreground_white_threshold=int(
            raw_options.get("--foreground-white-threshold", "215")
        ),
        score_threshold=float(raw_options.get("--score-threshold", "0.15")),
        detection_merge=DetectionMergeOptions(
            strategy=_merge_strategy(raw_options.get("--merge-strategy", "union")),
            edge_adjacency_gap=float(raw_options.get("--merge-edge-gap", "16.0")),
            min_orthogonal_overlap_ratio=float(
                raw_options.get("--merge-min-orthogonal-overlap-ratio", "0.25")
            ),
            scale_marker_coverage_ratio=float(
                raw_options.get("--scale-marker-coverage-ratio", "0.8")
            ),
            nms_iou_threshold=float(raw_options.get("--nms-iou-threshold", "0.1")),
            nms_containment_threshold=float(
                raw_options.get("--nms-containment-threshold", "0.99")
            ),
        ),
        scale_removal=ScaleRemovalOptions(
            padding_ratio=float(
                raw_options.get("--scale-removal-padding-ratio", "0.35")
            )
        ),
    )
    return tuple(passthrough), options
