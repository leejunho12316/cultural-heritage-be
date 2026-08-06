"""Materialize anomaly rough-mask runner outputs for normalization."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from modules.shared import ContractValidationError

if TYPE_CHECKING:
    from modules.prompt_generating import PromptRecord
    from modules.rough_masking.artifacts.records import JsonValue
    from modules.rough_masking.contracts import AdapterRequest

PNG_HEADER: Final = b"\x89PNG\r\n\x1a\n"
JPEG_HEADER: Final = b"\xff\xd8"


@dataclass(frozen=True, slots=True)
class AnomalyMaskOutput:
    """One runner-produced anomaly mask ready for rough candidate normalization."""

    prompt: PromptRecord
    score: float
    bbox_xyxy: tuple[float, float, float, float]
    mask_png: bytes
    overlay_jpeg: bytes
    quality_filter_version: str
    quality_score: float
    mask_area_ratio: float
    bbox_fill_ratio: float
    boundary_pixel_ratio: float
    perimeter_coverage_ratio: float
    border_touch_count: int
    component_count: int
    largest_component_ratio: float


@dataclass(frozen=True, slots=True)
class RejectedMaskOutput:
    """One runner-produced rejection record for mask-quality diagnostics."""

    prompt: PromptRecord
    score: float
    bbox_xyxy: tuple[float, float, float, float]
    reject_reason: str
    quality_filter_version: str
    quality_score: float | None
    mask_area_ratio: float
    bbox_fill_ratio: float | None
    boundary_pixel_ratio: float | None
    perimeter_coverage_ratio: float | None
    border_touch_count: int | None
    component_count: int | None
    largest_component_ratio: float | None


type MaskOutput = AnomalyMaskOutput | RejectedMaskOutput


def _raise_contract(field: str, reason: str) -> None:
    raise ContractValidationError(field, reason)


def _validate_bbox(
    request: AdapterRequest, bbox_xyxy: tuple[float, float, float, float]
) -> None:
    left, top, right, bottom = bbox_xyxy
    values = (left, top, right, bottom)
    if not all(math.isfinite(value) for value in values) or min(values) < 0:
        _raise_contract("bbox", "bbox_not_finite_or_negative")
    if right <= left or bottom <= top:
        _raise_contract("bbox", "bbox_invalid_order")
    if right > request.image_width_px or bottom > request.image_height_px:
        _raise_contract("bbox_outside_roi", "bbox outside ROI view")


def _validate_quality(output: MaskOutput) -> None:
    values = (
        output.quality_score,
        output.mask_area_ratio,
        output.bbox_fill_ratio,
        output.boundary_pixel_ratio,
        output.perimeter_coverage_ratio,
        output.largest_component_ratio,
    )
    for value in values:
        if value is not None and (not math.isfinite(value) or not 0.0 <= value <= 1.0):
            _raise_contract("quality_metric", "quality metric outside unit interval")
    counts = (output.border_touch_count, output.component_count)
    for count in counts:
        if count is not None and count < 0:
            _raise_contract("quality_metric", "quality count must be non-negative")


def _validate_prompt(request: AdapterRequest, output: MaskOutput) -> None:
    if output.prompt not in request.prompts:
        _raise_contract("prompt", "prompt_not_locked_seed")


def _validate_output(request: AdapterRequest, output: MaskOutput) -> None:
    _validate_bbox(request, output.bbox_xyxy)
    _validate_quality(output)
    _validate_prompt(request, output)
    if isinstance(output, RejectedMaskOutput):
        if not output.reject_reason:
            _raise_contract("reject_reason", "reject reason required")
        return
    if not output.mask_png.startswith(PNG_HEADER):
        _raise_contract("rough_mask_png", "invalid PNG signature")
    if not output.overlay_jpeg.startswith(JPEG_HEADER):
        _raise_contract("rough_overlay_jpeg", "invalid JPEG signature")


def _validate_paths(request: AdapterRequest) -> None:
    lane_root = request.lane_output_dir.resolve()
    records_path = request.records_json.resolve()
    if not records_path.is_relative_to(lane_root):
        _raise_contract("records_json", "must stay inside lane output dir")


def _quality_fields(output: MaskOutput) -> dict[str, JsonValue]:
    return {
        "quality_filter_version": output.quality_filter_version,
        "quality_score": output.quality_score,
        "mask_area_ratio": output.mask_area_ratio,
        "bbox_fill_ratio": output.bbox_fill_ratio,
        "boundary_pixel_ratio": output.boundary_pixel_ratio,
        "perimeter_coverage_ratio": output.perimeter_coverage_ratio,
        "border_touch_count": output.border_touch_count,
        "component_count": output.component_count,
        "largest_component_ratio": output.largest_component_ratio,
    }


def _accepted_record(
    request: AdapterRequest,
    output: AnomalyMaskOutput,
    mask_path: str,
    overlay_path: str,
) -> dict[str, JsonValue]:
    record: dict[str, JsonValue] = {
        "accepted": True,
        "image": request.view.image_id,
        "prompt": output.prompt.prompt_text,
        "score": output.score,
        "bbox_xyxy": list(output.bbox_xyxy),
        "mask_path": mask_path,
        "mask_semantics": "anomaly_region",
        "overlay_path": overlay_path,
        "prompt_pack_id": output.prompt.metadata.prompt_pack_id,
        "generation_lane": output.prompt.metadata.model_lane.value,
    }
    record.update(_quality_fields(output))
    return record


def _rejected_record(
    request: AdapterRequest, output: RejectedMaskOutput
) -> dict[str, JsonValue]:
    record: dict[str, JsonValue] = {
        "accepted": False,
        "reject_reason": output.reject_reason,
        "image": request.view.image_id,
        "prompt": output.prompt.prompt_text,
        "score": output.score,
        "bbox_xyxy": list(output.bbox_xyxy),
        "prompt_pack_id": output.prompt.metadata.prompt_pack_id,
        "generation_lane": output.prompt.metadata.model_lane.value,
    }
    record.update(_quality_fields(output))
    return record


def materialize_anomaly_outputs(
    request: AdapterRequest, outputs: tuple[MaskOutput, ...]
) -> None:
    """Write anomaly mask assets and records for one invoked rough-mask lane."""
    _validate_paths(request)
    for output in outputs:
        _validate_output(request, output)
    masks_dir = request.lane_output_dir / "masks"
    overlays_dir = request.lane_output_dir / "overlays"
    masks_dir.mkdir(parents=True, exist_ok=True)
    overlays_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, JsonValue]] = []
    accepted_index = 0
    for output in outputs:
        if isinstance(output, RejectedMaskOutput):
            records.append(_rejected_record(request, output))
            continue
        mask_path = f"masks/anomaly-{accepted_index:04d}.png"
        overlay_path = f"overlays/anomaly-{accepted_index:04d}.jpg"
        record = _accepted_record(request, output, mask_path, overlay_path)
        _ = (request.lane_output_dir / mask_path).write_bytes(output.mask_png)
        _ = (request.lane_output_dir / overlay_path).write_bytes(output.overlay_jpeg)
        records.append(record)
        accepted_index += 1
    _ = request.records_json.write_text(
        json.dumps(records, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    )
