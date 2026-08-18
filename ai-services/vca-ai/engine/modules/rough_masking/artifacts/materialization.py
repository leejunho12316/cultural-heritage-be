"""Materialize anomaly rough-mask runner outputs for normalization."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from modules.shared import ContractValidationError

if TYPE_CHECKING:
    from pathlib import Path

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


# bbox 좌표가 유효한 범위인지 검증한다 (유한값, 순서, ROI 경계 이내).
# _validate_output에서 accepted/rejected 레코드 모두에 대해 호출된다.
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


# 품질 지표들이 [0,1] 구간과 음이 아닌 정수 범위를 벗어나지 않는지 검증한다.
# _validate_output에서 호출되며, 저장 전 마지막 안전망 역할을 한다.
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


# 출력에 쓰인 프롬프트가 요청에 포함된 locked seed 프롬프트인지 확인한다.
# 임의 프롬프트로 조작된 결과가 기록되는 것을 막는다.
def _validate_prompt(request: AdapterRequest, output: MaskOutput) -> None:
    if output.prompt not in request.prompts:
        _raise_contract("prompt", "prompt_not_locked_seed")


# 하나의 러너 출력(accepted 또는 rejected)에 대해 bbox/품질/프롬프트 검증을
# 모두 수행하고, accepted인 경우 PNG/JPEG 시그니처까지 확인한다.
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


# records_json이 lane_output_dir 하위에 있는지 확인한다 (경로 이탈 방지).
def _validate_paths(request: AdapterRequest) -> None:
    lane_root = request.lane_output_dir.resolve()
    records_path = request.records_json.resolve()
    if not records_path.is_relative_to(lane_root):
        _raise_contract("records_json", "must stay inside lane output dir")


# request.view가 원본 사진에서 차지하는 사각형을 [left, top, right, bottom]으로
# 반환한다. coordinate_transform이 없는 뷰(FULL_IMAGE)는 크롭이 아예 없었다는
# 뜻이라 bbox_xyxy가 이미 원본 좌표계다 - 그래서 (0, 0, 뷰 자신의 폭, 높이)를
# 항등 원점으로 쓴다.
def _view_origin_xyxy(request: AdapterRequest) -> list[float]:
    transform = request.view.coordinate_transform
    if transform is None:
        return [0.0, 0.0, float(request.image_width_px), float(request.image_height_px)]
    source_bbox = transform.source_bbox
    return [
        source_bbox.left,
        source_bbox.top,
        source_bbox.left + source_bbox.width,
        source_bbox.top + source_bbox.height,
    ]


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


# accepted 마스크 출력을 JSON 레코드로 직렬화한다 (mask/overlay 경로, 품질 지표 포함).
# 저장 후 rough candidate 정규화 단계에서 이 레코드를 다시 읽는다.
def _accepted_record(
    request: AdapterRequest,
    output: AnomalyMaskOutput,
    mask_path: str,
    overlay_path: str,
    image_path: Path,
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
        "view_origin_xyxy": _view_origin_xyxy(request),
        "view_image_path": str(image_path),
    }
    record.update(_quality_fields(output))
    return record


# rejected 마스크 출력을 진단용 JSON 레코드로 직렬화한다 (reject_reason 포함).
def _rejected_record(
    request: AdapterRequest, output: RejectedMaskOutput, image_path: Path
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
        "view_origin_xyxy": _view_origin_xyxy(request),
        "view_image_path": str(image_path),
    }
    record.update(_quality_fields(output))
    return record


def materialize_anomaly_outputs(
    request: AdapterRequest, outputs: tuple[MaskOutput, ...], image_path: Path
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
            records.append(_rejected_record(request, output, image_path))
            continue
        mask_path = f"masks/anomaly-{accepted_index:04d}.png"
        overlay_path = f"overlays/anomaly-{accepted_index:04d}.jpg"
        record = _accepted_record(
            request, output, mask_path, overlay_path, image_path
        )
        _ = (request.lane_output_dir / mask_path).write_bytes(output.mask_png)
        _ = (request.lane_output_dir / overlay_path).write_bytes(output.overlay_jpeg)
        records.append(record)
        accepted_index += 1
    _ = request.records_json.write_text(
        json.dumps(records, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    )
