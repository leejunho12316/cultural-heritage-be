"""JSON boundary for the standalone anomaly grouping runner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from modules.anomaly_grouping.models import (
    ANOMALY_GROUPING_REQUEST_SCHEMA,
    AnomalyCandidate,
    AnomalyGroupingRequest,
    AnomalyGroupingResult,
    BoundingBox,
    CandidateEvidence,
    MaskReference,
)
from modules.anomaly_grouping.serialization import result_payload
from modules.anomaly_grouping.shared_contracts import (
    JsonObject,
    JsonValue,
    parse_hybrid_descriptor,
    parse_relation_authority_input,
)
from modules.shared import CandidateId, ContractValidationError, RagAccountingStatus

if TYPE_CHECKING:
    from collections.abc import Mapping

    from modules.shared import HybridDescriptor, RelationAuthorityInput

_BBOX_COORDINATE_COUNT: Final = 4


# 독립 실행 CLI(runner.py)의 진입점에서 호출되어, 요청 JSON 파일을 타입화된
# AnomalyGroupingRequest로 변환한다.
def read_request(path: Path) -> AnomalyGroupingRequest:
    """Read and parse one anomaly grouping request JSON file."""
    raw_payload = cast("JsonValue", json.loads(path.read_text(encoding="utf-8")))
    payload = _json_value(raw_payload)
    if not isinstance(payload, dict):
        field_name = "request"
        reason = "must be a JSON object"
        raise ContractValidationError(field_name, reason)
    return parse_request(payload)


# runner.py에서 결과를 결정적(정렬된 키, 압축된 구분자) JSON으로 기록할 때
# 사용한다.
def write_result(path: Path, result: AnomalyGroupingResult) -> None:
    """Write one deterministic anomaly grouping result JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        json.dumps(result_payload(result), sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


# read_request가 호출하는 핵심 파싱 로직. 신뢰할 수 없는 JSON 경계에서 스키마
# 버전과 필수 필드를 검증하며, 실패 시 ContractValidationError를 던진다.
def parse_request(payload: Mapping[str, JsonValue]) -> AnomalyGroupingRequest:
    """Parse a request payload at the untrusted JSON boundary."""
    if _string(payload, "schema") != ANOMALY_GROUPING_REQUEST_SCHEMA:
        field_name = "schema"
        reason = "unsupported anomaly grouping request"
        raise ContractValidationError(field_name, reason)
    return AnomalyGroupingRequest(
        _candidates(payload),
        Path(_string(payload, "mask_output_dir")),
    )


def _candidates(payload: Mapping[str, JsonValue]) -> tuple[AnomalyCandidate, ...]:
    return tuple(_candidate(item) for item in _objects(payload, "candidates"))


# 후보 하나의 JSON 표현을 AnomalyCandidate로 변환한다.
def _candidate(payload: Mapping[str, JsonValue]) -> AnomalyCandidate:
    return AnomalyCandidate(
        CandidateId(_string(payload, "candidate_id")),
        _string(payload, "image_id"),
        _string(payload, "source_object_id"),
        _string(payload, "source_view_id"),
        _string(payload, "seed_lane"),
        _string(payload, "seed_prompt"),
        _bbox(payload),
        _mask(payload),
        _evidence(payload),
        _string_or_none(payload, "duplicate_suppression_key"),
        source_tile_view_id=_string_or_none(payload, "source_tile_view_id"),
    )


# _candidate에서 mask 필드를 MaskReference로 변환한다.
def _mask(payload: Mapping[str, JsonValue]) -> MaskReference:
    mask = payload.get("mask")
    if not isinstance(mask, dict):
        field_name = "mask"
        reason = "must be an object"
        raise ContractValidationError(field_name, reason)
    return MaskReference(_string(mask, "path"), _string(mask, "sha256"))


# _candidate에서 evidence 필드를 CandidateEvidence로 변환한다. hybrid_descriptor
# 유무에 따라 relation_authority_input(C-004 입력) 파싱 여부가 갈린다.
def _evidence(payload: Mapping[str, JsonValue]) -> CandidateEvidence:
    evidence = payload.get("evidence")
    if evidence is None:
        return CandidateEvidence()
    if not isinstance(evidence, dict):
        field_name = "evidence"
        reason = "must be an object"
        raise ContractValidationError(field_name, reason)
    descriptor = _optional_hybrid_descriptor(evidence)
    return CandidateEvidence(
        _string_default(evidence, "concept_family", "unknown"),
        _strings_default(evidence, "descriptor_tokens"),
        _strings_default(evidence, "concept_card_ids"),
        _string_default(evidence, "provenance_strength", "unknown"),
        _strings_default(evidence, "evidence_flags"),
        _rag_status(evidence),
        _float_or_none(evidence, "visual_cue_confidence"),
        descriptor,
        _optional_relation_input(evidence, descriptor),
    )


# _candidate에서 bbox_xyxy 4개 숫자를 검증해 BoundingBox로 만든다.
def _bbox(payload: Mapping[str, JsonValue]) -> BoundingBox:
    values = _numbers(payload, "bbox_xyxy")
    if len(values) != _BBOX_COORDINATE_COUNT:
        field_name = "bbox_xyxy"
        reason = "must contain four numbers"
        raise ContractValidationError(field_name, reason)
    return BoundingBox(*values)


def _objects(
    payload: Mapping[str, JsonValue], field_name: str
) -> tuple[Mapping[str, JsonValue], ...]:
    value = payload.get(field_name)
    if not isinstance(value, list):
        raise ContractValidationError(field_name, "must be a list of objects")
    objects: list[Mapping[str, JsonValue]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ContractValidationError(field_name, "must be a list of objects")
        objects.append(item)
    return tuple(objects)


def _string(payload: Mapping[str, JsonValue], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str):
        raise ContractValidationError(field_name, "must be a string")
    return value


def _string_default(
    payload: Mapping[str, JsonValue], field_name: str, default: str
) -> str:
    value = payload.get(field_name, default)
    if not isinstance(value, str):
        raise ContractValidationError(field_name, "must be a string")
    return value


def _string_or_none(payload: Mapping[str, JsonValue], field_name: str) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractValidationError(field_name, "must be a string")
    return value


# _evidence에서 hybrid_descriptor(C-004 브리지 입력)가 있으면 파싱하고, 없으면
# None을 반환한다. relation_authority_input 파싱 가능 여부를 결정한다.
def _optional_hybrid_descriptor(
    payload: Mapping[str, JsonValue],
) -> HybridDescriptor | None:
    value = payload.get("hybrid_descriptor")
    if value is None:
        return None
    if not isinstance(value, dict):
        field_name = "hybrid_descriptor"
        reason = "must be an object"
        raise ContractValidationError(field_name, reason)
    return parse_hybrid_descriptor(value)


# _evidence에서 relation_authority_input을 파싱한다. hybrid_descriptor가 먼저
# 파싱되어 있어야만 유효하며, 없는데 값이 오면 계약 오류를 낸다.
def _optional_relation_input(
    payload: Mapping[str, JsonValue],
    descriptor: HybridDescriptor | None,
) -> RelationAuthorityInput | None:
    value = payload.get("relation_authority_input")
    if value is None:
        return None
    if descriptor is None:
        field_name = "relation_authority_input"
        reason = "requires hybrid_descriptor"
        raise ContractValidationError(
            field_name,
            reason,
        )
    if not isinstance(value, dict):
        field_name = "relation_authority_input"
        reason = "must be an object"
        raise ContractValidationError(field_name, reason)
    return parse_relation_authority_input(value, descriptor)


# _evidence에서 rag_status 문자열을 RagAccountingStatus enum으로 변환한다.
def _rag_status(payload: Mapping[str, JsonValue]) -> RagAccountingStatus:
    raw_status = _string_default(
        payload,
        "rag_status",
        RagAccountingStatus.COMPLETED.value,
    )
    try:
        return RagAccountingStatus(raw_status)
    except ValueError as error:
        field_name = "rag_status"
        raise ContractValidationError(field_name, raw_status) from error


def _strings_default(
    payload: Mapping[str, JsonValue], field_name: str
) -> tuple[str, ...]:
    return _string_sequence(payload.get(field_name, []), field_name)


def _string_sequence(value: JsonValue, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ContractValidationError(field_name, "must be a list of strings")
    values: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ContractValidationError(field_name, "must be a list of strings")
        values.append(item)
    return tuple(values)


def _numbers(payload: Mapping[str, JsonValue], field_name: str) -> tuple[float, ...]:
    value = payload.get(field_name)
    if not isinstance(value, list):
        raise ContractValidationError(field_name, "must be a list of numbers")
    values: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int | float):
            raise ContractValidationError(field_name, "must be a list of numbers")
        values.append(float(item))
    return tuple(values)


def _float_or_none(payload: Mapping[str, JsonValue], field_name: str) -> float | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ContractValidationError(field_name, "must be a number")
    return float(value)


def _json_value(value: JsonValue) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    converted: JsonObject = {}
    for key, item in value.items():
        converted[key] = _json_value(item)
    return converted
