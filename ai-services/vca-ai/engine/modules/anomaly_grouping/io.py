"""JSON boundary for the standalone anomaly grouping runner."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final, cast

from modules.anomaly_grouping.models import (
    ANOMALY_GROUPING_REQUEST_SCHEMA,
    AnomalyCandidate,
    AnomalyGroupingRequest,
    AnomalyGroupingResult,
    BoundingBox,
    CandidateEvidence,
    MergePhase,
    PreviousSuppression,
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
    from pathlib import Path

    from modules.shared import HybridDescriptor, RelationAuthorityInput

_BBOX_COORDINATE_COUNT: Final = 4


def read_request(path: Path) -> AnomalyGroupingRequest:
    """Read and parse one anomaly grouping request JSON file."""
    raw_payload = cast("JsonValue", json.loads(path.read_text(encoding="utf-8")))
    payload = _json_value(raw_payload)
    if not isinstance(payload, dict):
        field_name = "request"
        reason = "must be a JSON object"
        raise ContractValidationError(field_name, reason)
    return parse_request(payload)


def write_result(path: Path, result: AnomalyGroupingResult) -> None:
    """Write one deterministic anomaly grouping result JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        json.dumps(result_payload(result), sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def parse_request(payload: Mapping[str, JsonValue]) -> AnomalyGroupingRequest:
    """Parse a request payload at the untrusted JSON boundary."""
    if _string(payload, "schema") != ANOMALY_GROUPING_REQUEST_SCHEMA:
        field_name = "schema"
        reason = "unsupported anomaly grouping request"
        raise ContractValidationError(field_name, reason)
    return AnomalyGroupingRequest(
        _merge_phase(payload),
        _strings(payload, "seed_lane_priority"),
        _candidates(payload, "pre_rag_candidates"),
        _candidates(payload, "post_rag_candidates"),
        _suppressions(payload),
        tuple(
            CandidateId(value)
            for value in _strings(payload, "already_reopened_candidate_ids")
        ),
    )


def _candidates(
    payload: Mapping[str, JsonValue], field_name: str
) -> tuple[AnomalyCandidate, ...]:
    return tuple(_candidate(item) for item in _objects(payload, field_name))


def _candidate(payload: Mapping[str, JsonValue]) -> AnomalyCandidate:
    return AnomalyCandidate(
        CandidateId(_string(payload, "candidate_id")),
        _string(payload, "source_object_id"),
        _string(payload, "source_view_id"),
        _string(payload, "seed_lane"),
        _string(payload, "seed_prompt"),
        _bbox(payload),
        _evidence(payload),
        _candidate_id_or_none(payload, "explicit_pre_rag_parent_id"),
        _string_or_none(payload, "duplicate_suppression_key"),
    )


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


def _bbox(payload: Mapping[str, JsonValue]) -> BoundingBox:
    values = _numbers(payload, "bbox_xyxy")
    if len(values) != _BBOX_COORDINATE_COUNT:
        field_name = "bbox_xyxy"
        reason = "must contain four numbers"
        raise ContractValidationError(field_name, reason)
    return BoundingBox(*values)


def _suppressions(payload: Mapping[str, JsonValue]) -> tuple[PreviousSuppression, ...]:
    return tuple(
        PreviousSuppression(
            CandidateId(_string(item, "candidate_id")),
            CandidateId(_string(item, "parent_candidate_id")),
            _string(item, "same_anomaly_group_id"),
        )
        for item in _objects(payload, "previous_suppressions")
    )


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


def _candidate_id_or_none(
    payload: Mapping[str, JsonValue], field_name: str
) -> CandidateId | None:
    value = _string_or_none(payload, field_name)
    return CandidateId(value) if value is not None else None


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


def _merge_phase(payload: Mapping[str, JsonValue]) -> MergePhase:
    raw_phase = _string(payload, "phase")
    try:
        return MergePhase(raw_phase)
    except ValueError as error:
        field_name = "phase"
        raise ContractValidationError(field_name, raw_phase) from error


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


def _strings(payload: Mapping[str, JsonValue], field_name: str) -> tuple[str, ...]:
    value = payload.get(field_name)
    return _string_sequence(value, field_name)


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
