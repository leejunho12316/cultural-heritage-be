"""Rough-mask record adapter for Qwen refinement requests."""

from __future__ import annotations

import math
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import NoReturn

from modules.prompt_generating import PromptRecord, static_seed_minimal_pack
from modules.rag.operations.candidate_sidecar_artifacts import (
    RoughRagCandidate,
    read_rough_records,
)
from modules.rough_masking import (
    AssetReference,
    CandidateStatus,
    RawDetectorCandidate,
    seed_thresholds,
)
from modules.rough_masking.artifacts.records import (
    JsonRecord,
    JsonValue,
    decode_records,
)
from modules.rough_masking.contracts import SAM2_MODEL_ID
from modules.shared import (
    ContractValidationError,
    DetectorLane,
    ImageId,
    detector_to_rag_lane,
    parse_detector_lane,
)

_BBOX_COORDINATES = 4
_OBJECT_ID_MINIMUM_PARTS = 2
_TILE_VIEW_ID_MINIMUM_PARTS = 4
_TILE_VIEW_SEGMENT_INDEX = 3
_NON_TILE_VIEW_SEGMENTS = frozenset({"object", "anomaly_merged"})


@dataclass(frozen=True, slots=True)
class RoughQwenCandidate:
    """RAG-compatible rough candidate plus Qwen-ready detector candidate."""

    rough: RoughRagCandidate
    candidate: RawDetectorCandidate


def rough_qwen_candidates(
    rough_root: Path,
    asset_root: Path | None = None,
) -> tuple[RoughQwenCandidate, ...]:
    """Read accepted rough records as Qwen-ready candidates."""
    resolved_asset_root = rough_root if asset_root is None else asset_root
    rough_candidates = read_rough_records(rough_root)
    return tuple(
        _qwen_candidate(rough_root, resolved_asset_root, rough)
        for rough in rough_candidates
    )


# 하나의 rough 레코드를 읽어 RawDetectorCandidate로 재구성한다.
# rough_qwen_candidates가 각 레코드마다 호출하며, 재구성 실패 시
# ContractValidationError를 발생시킨다.
def _qwen_candidate(
    rough_root: Path,
    asset_root: Path,
    rough: RoughRagCandidate,
) -> RoughQwenCandidate:
    record = _record(rough_root / rough.rough_record_path, rough.rough_record_index)
    detector_lane = parse_detector_lane(rough.lane)
    lane_root = rough_root / Path(rough.rough_record_path).parent
    candidate = RawDetectorCandidate(
        rough.candidate_id,
        ImageId(_string(record, "image")),
        detector_lane,
        CandidateStatus.ACCEPTED,
        rough.prompt_text,
        _float(record, "score"),
        _bbox(record, "bbox_xyxy"),
        _asset(asset_root, lane_root, _string(record, "mask_path"), "image/png"),
        _asset(asset_root, lane_root, _string(record, "overlay_path"), "image/jpeg"),
        f"rough-mask:{detector_lane.value}",
        SAM2_MODEL_ID,
        seed_thresholds(detector_lane),
        _prompt(detector_lane, rough.prompt_text).metadata,
        _source_view_id(rough),
        _object_id(rough),
        _tile_view_id(rough),
        (),
        _bbox(record, "view_origin_xyxy"),
        _view_image_path(asset_root, record),
    )
    return RoughQwenCandidate(rough, candidate)


# rough_record_index로 저장된 위치의 레코드를 다시 읽는다. records.json의
# 항목 순서가 저장 시점과 동일하게 유지된다는 전제에 의존한다.
def _record(records_path: Path, index: int) -> JsonRecord:
    rows = decode_records(records_path.read_text(encoding="utf-8"))
    if rows is None or index >= len(rows):
        _raise_contract("records_json", "record index missing")
    return rows[index]


# mask/overlay 상대 경로가 rough record 디렉터리와 asset_root를 벗어나지
# 않는지 검증하고 AssetReference로 만든다(해시는 다시 계산한다).
def _asset(
    asset_root: Path,
    lane_root: Path,
    relative_asset_path: str,
    media_type: str,
) -> AssetReference:
    raw_path = Path(relative_asset_path)
    if raw_path.is_absolute() or ".." in raw_path.parts:
        _raise_contract("asset_path", "must stay inside rough record directory")
    resolved_root = asset_root.resolve()
    resolved_path = (lane_root / raw_path).resolve()
    if not resolved_path.is_relative_to(resolved_root):
        _raise_contract("asset_path", "must stay inside rough root")
    if not resolved_path.is_file():
        _raise_contract("asset_path", "asset missing")
    return AssetReference(
        resolved_path.relative_to(resolved_root).as_posix(),
        sha256(resolved_path.read_bytes()).hexdigest(),
        media_type,
    )


# 저장된 prompt_text를 신뢰하지 않고 static_seed_minimal_pack의 잠긴
# 프롬프트 목록과 다시 대조해 PromptRecord를 복원한다.
def _prompt(detector_lane: DetectorLane, prompt_text: str) -> PromptRecord:
    rag_lane = detector_to_rag_lane(detector_lane)
    for prompt in static_seed_minimal_pack.records:
        if prompt.metadata.model_lane is rag_lane and prompt.prompt_text == prompt_text:
            return prompt
    return _raise_contract("prompt", "must match locked rough seed prompt")


# bbox 필드(bbox_xyxy 또는 view_origin_xyxy)를 파싱하고 좌표가 양수이며 순서가
# 올바른지 검증한다.
def _bbox(record: JsonRecord, field: str) -> tuple[float, float, float, float]:
    raw_bbox = record.get(field)
    if not isinstance(raw_bbox, list) or len(raw_bbox) != _BBOX_COORDINATES:
        _raise_contract(field, "must contain four numbers")
    values = tuple(_finite_number(value, field) for value in raw_bbox)
    left, top, right, bottom = values
    if min(values) < 0 or right <= left or bottom <= top:
        _raise_contract(field, "must be a positive box")
    return left, top, right, bottom


# view_image_path(뷰를 만들 때 탐지기에 실제로 입력된 이미지의 절대경로)가
# asset_root 밖으로 벗어나지 않고 실제 파일로 존재하는지 검증한다. mask/overlay와
# 달리 이 경로는 preprocessing 소유(객체 크롭, OWLv2 타일)일 수도, rough_masking
# 소유(GroundingDINO 타일, 병합된 타일 크롭)일 수도 있어 lane_root 상대경로로
# 표현할 수 없다 - 그래서 절대경로로 저장하고 asset_root 포함 여부만 검증한다.
def _view_image_path(asset_root: Path, record: JsonRecord) -> Path:
    raw_path = _string(record, "view_image_path")
    resolved_root = asset_root.resolve()
    resolved_path = Path(raw_path).resolve()
    if not resolved_path.is_relative_to(resolved_root):
        _raise_contract("view_image_path", "must stay inside asset root")
    if not resolved_path.is_file():
        _raise_contract("view_image_path", "asset missing")
    return resolved_path


def _float(record: JsonRecord, field: str) -> float:
    return _finite_number(record.get(field), field)


def _finite_number(value: JsonValue | None, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        _raise_contract(field, "must be a number")
    number = float(value)
    if not math.isfinite(number):
        _raise_contract(field, "must be finite")
    return number


def _string(record: JsonRecord, field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        _raise_contract(field, "must be a non-blank string")
    return value


def _source_view_id(rough: RoughRagCandidate) -> str:
    return f"rough-source:{rough.candidate_id}"


def _object_id(rough: RoughRagCandidate) -> str | None:
    # rough-mask 출력 경로 구조 <lane>/<object_id>/<lane>/<view_segment>/records.json
    # 에 의존한다 (rough_masking startup_runner._lane_view_paths 참고). 구조가
    # 바뀌면 예외 없이 조용히 잘못된 값을 반환한다.
    raw_path = Path(rough.rough_record_path)
    return (
        raw_path.parts[1] if len(raw_path.parts) >= _OBJECT_ID_MINIMUM_PARTS else None
    )


# source_tile_view_id는 records.json 필드로 저장되지 않고 경로의
# view_segment(네 번째 조각)에서만 파생된다 - _object_id와 같은 경로 규약에
# 의존한다. "object"(오브젝트 크롭 전체)와 "anomaly_merged"(anomaly_grouping/
# pre_refinement_merge.py가 만든, 물리적으로 같은 특이점끼리 이미 합친 결과)는
# 특정 타일 하나를 가리키지 않으므로 둘 다 None으로 취급한다.
def _tile_view_id(rough: RoughRagCandidate) -> str | None:
    raw_path = Path(rough.rough_record_path)
    if len(raw_path.parts) < _TILE_VIEW_ID_MINIMUM_PARTS:
        return None
    segment = raw_path.parts[_TILE_VIEW_SEGMENT_INDEX]
    return None if segment in _NON_TILE_VIEW_SEGMENTS else segment


def _raise_contract(field: str, reason: str) -> NoReturn:
    raise ContractValidationError(field, reason)
