"""Validation of accepted detector records into rough candidates."""

import json
import math
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Final

from modules.prompt_generating import PromptRecord
from modules.rough_masking.artifacts.assets import AssetReference, normalize_asset
from modules.rough_masking.artifacts.records import JsonValue
from modules.rough_masking.contracts import AdapterRequest, SeedThresholds
from modules.shared import CandidateId, DetectorLane, ImageId, PromptMetadata


class CandidateStatus(StrEnum):
    """Only normalized accepted records become rough candidates."""

    ACCEPTED = "accepted"


BBOX_COORDINATE_COUNT: Final = 4
ROUGH_MASK_SEMANTICS: Final = "anomaly_region"
IDENTITY_RECORD_KEYS: Final = (
    "accepted",
    "bbox_xyxy",
    "generation_lane",
    "image",
    "mask_path",
    "mask_semantics",
    "overlay_path",
    "prompt",
    "prompt_pack_id",
    "score",
)


@dataclass(frozen=True, slots=True)
class RawDetectorCandidate:
    """Validated common representation for an accepted detector record."""

    candidate_id: CandidateId
    image_id: ImageId
    lane: DetectorLane
    status: CandidateStatus
    executable_prompt: str
    score: float
    bbox_xyxy: tuple[float, float, float, float]
    rough_mask: AssetReference
    overlay: AssetReference
    detector_model_id: str
    sam2_model_id: str
    threshold_config: SeedThresholds
    prompt_provenance: PromptMetadata
    source_view_id: str
    source_object_id: str | None
    source_tile_view_id: str | None
    diagnostics: tuple[str, ...]
    view_origin_xyxy: tuple[float, float, float, float]
    view_image_path: Path | None = None


@dataclass(frozen=True, slots=True)
class CandidateParts:
    """Normalized values required to construct an accepted candidate."""

    score: float
    image: str
    bbox_xyxy: tuple[float, float, float, float]
    mask: AssetReference
    overlay: AssetReference
    prompt: PromptRecord


def _number(
    raw: JsonValue | None, missing: str, invalid: str
) -> tuple[float | None, str | None]:
    if raw is None:
        return None, missing
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None, invalid
    value = float(raw)
    return (value, None) if math.isfinite(value) else (None, invalid)


# accepted=False 레코드는 점수 없이 거절 사유만 전달하고, 그 외에는 score 필드를
# 숫자로 검증한다. _candidate_parts 파이프라인의 첫 단계다.
def _score(record: dict[str, JsonValue]) -> tuple[float | None, str | None]:
    if record.get("accepted") is False:
        return None, f"record_rejected:{record.get('reject_reason', 'unspecified')}"
    return _number(record.get("score"), "score_missing", "score_invalid")


# 레코드의 image 필드가 요청 뷰의 image_id와 일치하는지 확인한다.
def _image(
    request: AdapterRequest, record: dict[str, JsonValue]
) -> tuple[str | None, str | None]:
    image = record.get("image")
    if not isinstance(image, str) or image != request.view.image_id:
        return None, "image_id_missing"
    return image, None


# bbox_xyxy를 파싱하고 좌표 순서·범위·이미지 경계를 검증한다.
def _bbox(
    request: AdapterRequest, record: dict[str, JsonValue]
) -> tuple[tuple[float, float, float, float] | None, str | None]:
    bbox = record.get("bbox_xyxy")
    if not isinstance(bbox, list) or len(bbox) != BBOX_COORDINATE_COUNT:
        return None, "bbox_missing"
    values: list[float] = []
    for raw in bbox:
        value, diagnostic = _number(raw, "bbox_missing", "bbox_not_finite")
        if diagnostic is not None or value is None:
            return None, diagnostic
        values.append(value)
    left, top, right, bottom = values
    if min(values) < 0:
        return None, "bbox_negative"
    if right <= left or bottom <= top:
        return None, "bbox_invalid_order"
    if right > request.image_width_px or bottom > request.image_height_px:
        return None, "bbox_outside_image"
    return (left, top, right, bottom), None


# mask_semantics가 anomaly_region인지 확인한 뒤 mask/overlay 자산을 정규화한다.
def _assets(
    request: AdapterRequest, record: dict[str, JsonValue]
) -> tuple[tuple[AssetReference, AssetReference] | None, str | None]:
    semantics = record.get("mask_semantics")
    if semantics is None:
        return None, "mask_semantics_missing"
    if semantics != ROUGH_MASK_SEMANTICS:
        return None, "mask_semantics_invalid"
    mask, diagnostic = normalize_asset(
        request.lane_output_dir, record.get("mask_path"), "mask"
    )
    if diagnostic is not None or mask is None:
        return None, diagnostic
    overlay, diagnostic = normalize_asset(
        request.lane_output_dir, record.get("overlay_path"), "overlay"
    )
    if diagnostic is not None or overlay is None:
        return None, diagnostic
    return (mask, overlay), None


# 레코드의 prompt 텍스트를 요청에 잠긴 seed 프롬프트 목록과 대조하고,
# prompt_pack_id·generation_lane까지 일치하는지 확인한다.
def _prompt(
    request: AdapterRequest, record: dict[str, JsonValue]
) -> tuple[PromptRecord | None, str | None]:
    prompt = record.get("prompt")
    matched = next(
        (item for item in request.prompts if item.prompt_text == prompt), None
    )
    if matched is None:
        return None, "prompt_not_locked_seed"
    if record.get("prompt_pack_id") != matched.metadata.prompt_pack_id:
        return None, "prompt_pack_id_mismatch"
    if record.get("generation_lane") != matched.metadata.model_lane.value:
        return None, "generation_lane_mismatch"
    return matched, None


# score/image/bbox/assets/prompt 검증을 순서대로 실행하는 파이프라인이며,
# 첫 실패 지점에서 즉시 진단 코드를 반환한다. normalize_candidate에서 호출된다.
def _candidate_parts(
    request: AdapterRequest, record: dict[str, JsonValue]
) -> tuple[CandidateParts | None, str | None]:
    score, diagnostic = _score(record)
    if diagnostic is not None or score is None:
        return None, diagnostic
    image, diagnostic = _image(request, record)
    if diagnostic is not None or image is None:
        return None, diagnostic
    bbox, diagnostic = _bbox(request, record)
    if diagnostic is not None or bbox is None:
        return None, diagnostic
    assets, diagnostic = _assets(request, record)
    if diagnostic is not None or assets is None:
        return None, diagnostic
    prompt, diagnostic = _prompt(request, record)
    if diagnostic is not None or prompt is None:
        return None, diagnostic
    return CandidateParts(score, image, bbox, *assets, prompt), None


# request.view가 원본 사진에서 차지하는 사각형을 [left, top, right, bottom]으로
# 반환한다 - materialization.py의 동일한 헬퍼와 같은 목적(원본 좌표 복원에 필요한
# 뷰 자신의 위치)이지만, 여기는 이미 만들어진 records.json이 아니라 방금 만든
# AdapterRequest에서 직접 계산한다는 점이 다르다. coordinate_transform이 없는
# 뷰(FULL_IMAGE)는 크롭이 아예 없었다는 뜻이라 bbox_xyxy가 이미 원본 좌표계다 -
# 그래서 (0, 0, 뷰 자신의 폭, 높이)를 항등 원점으로 쓴다.
def _view_origin_xyxy(request: AdapterRequest) -> tuple[float, float, float, float]:
    transform = request.view.coordinate_transform
    if transform is None:
        return (0.0, 0.0, float(request.image_width_px), float(request.image_height_px))
    source_bbox = transform.source_bbox
    return (
        source_bbox.left,
        source_bbox.top,
        source_bbox.left + source_bbox.width,
        source_bbox.top + source_bbox.height,
    )


def normalize_candidate(
    request: AdapterRequest, record: dict[str, JsonValue]
) -> tuple[RawDetectorCandidate | None, str | None]:
    """Normalize one accepted runner record or return its diagnostic."""
    parts, diagnostic = _candidate_parts(request, record)
    if diagnostic is not None or parts is None:
        return None, diagnostic
    identity_record = {
        key: record[key] for key in IDENTITY_RECORD_KEYS if key in record
    }
    identity = json.dumps(
        {
            "record": identity_record,
            "mask_sha256": parts.mask.sha256,
            "overlay_sha256": parts.overlay.sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    candidate_id = CandidateId(
        sha256(f"{request.lane}|{parts.image}|{identity}".encode()).hexdigest()
    )
    return RawDetectorCandidate(
        candidate_id,
        ImageId(parts.image),
        request.lane,
        CandidateStatus.ACCEPTED,
        parts.prompt.prompt_text,
        parts.score,
        parts.bbox_xyxy,
        parts.mask,
        parts.overlay,
        request.detector_model_id,
        request.sam2_model_id,
        request.threshold_config,
        parts.prompt.metadata,
        request.view.view_id,
        request.view.object_id,
        request.view.tile_view_id,
        (),
        _view_origin_xyxy(request),
    ), None
