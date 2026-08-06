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
        _bbox(record),
        _asset(asset_root, lane_root, _string(record, "mask_path"), "image/png"),
        _asset(asset_root, lane_root, _string(record, "overlay_path"), "image/jpeg"),
        f"rough-mask:{detector_lane.value}",
        SAM2_MODEL_ID,
        seed_thresholds(detector_lane),
        _prompt(detector_lane, rough.prompt_text).metadata,
        _source_view_id(rough),
        _object_id(rough),
        None,
        (),
    )
    return RoughQwenCandidate(rough, candidate)


def _record(records_path: Path, index: int) -> JsonRecord:
    rows = decode_records(records_path.read_text(encoding="utf-8"))
    if rows is None or index >= len(rows):
        _raise_contract("records_json", "record index missing")
    return rows[index]


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


def _prompt(detector_lane: DetectorLane, prompt_text: str) -> PromptRecord:
    rag_lane = detector_to_rag_lane(detector_lane)
    for prompt in static_seed_minimal_pack.records:
        if prompt.metadata.model_lane is rag_lane and prompt.prompt_text == prompt_text:
            return prompt
    return _raise_contract("prompt", "must match locked rough seed prompt")


def _bbox(record: JsonRecord) -> tuple[float, float, float, float]:
    raw_bbox = record.get("bbox_xyxy")
    if not isinstance(raw_bbox, list) or len(raw_bbox) != _BBOX_COORDINATES:
        _raise_contract("bbox_xyxy", "must contain four numbers")
    values = tuple(_finite_number(value, "bbox_xyxy") for value in raw_bbox)
    left, top, right, bottom = values
    if min(values) < 0 or right <= left or bottom <= top:
        _raise_contract("bbox_xyxy", "must be a positive box")
    return left, top, right, bottom


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
    raw_path = Path(rough.rough_record_path)
    return (
        raw_path.parts[1] if len(raw_path.parts) >= _OBJECT_ID_MINIMUM_PARTS else None
    )


def _raise_contract(field: str, reason: str) -> NoReturn:
    raise ContractValidationError(field, reason)
