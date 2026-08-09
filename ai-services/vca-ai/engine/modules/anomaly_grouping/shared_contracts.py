"""Shared C-004 bridge preservation helpers for anomaly grouping."""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

from modules.shared import (
    CandidateId,
    ContractValidationError,
    ExportCitationBridge,
    HybridDescriptor,
    RagAccountingStatus,
    RelationAuthorityInput,
    RelationAuthorityOutcome,
    RelationAuthorityOutcomeState,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from modules.anomaly_grouping.models import AnomalyCandidate, RelationClass

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject = dict[str, JsonValue]


# relations.py의 _relation_group이 호출한다. C-004 브리지 입력(hybrid_descriptor
# + relation_authority_input)을 가진 후보들에 대해서만 공유 RelationAuthorityOutcome
# 레코드를 만든다(둘 중 하나라도 없으면 건너뜀).
def relation_outcomes(
    relation_class: RelationClass,
    candidates: Sequence[AnomalyCandidate],
) -> tuple[RelationAuthorityOutcome, ...]:
    """Build shared relation outcomes for candidates carrying C-004 inputs."""
    outcome_state = RelationAuthorityOutcomeState(relation_class.value)
    outcomes: list[RelationAuthorityOutcome] = []
    for candidate in candidates:
        descriptor = candidate.evidence.hybrid_descriptor
        relation_input = candidate.evidence.relation_authority_input
        if descriptor is None or relation_input is None:
            continue
        outcomes.append(
            RelationAuthorityOutcome(
                candidate.candidate_id,
                descriptor,
                relation_input,
                outcome_state,
                relation_input.rag_status,
                relation_input.evidence_flags,
            )
        )
    return tuple(outcomes)


# serialization.py의 result_payload가 relation_groups를 직렬화할 때 호출한다.
def relation_outcome_payload(outcome: RelationAuthorityOutcome) -> JsonObject:
    """Render a shared relation outcome into deterministic JSON fields."""
    return {
        "candidate_id": str(outcome.candidate_id),
        "evidence_flags": list(outcome.evidence_flags),
        "hybrid_descriptor": hybrid_descriptor_payload(outcome.hybrid_descriptor),
        "rag_status": outcome.rag_status.value,
        "relation_authority_input": relation_input_payload(
            outcome.relation_authority_input
        ),
        "relation_authority_outcome": outcome.relation_authority_outcome.value,
    }


# relation_outcome_payload에서 호출된다. hybrid_descriptor를 표시용 프로즈 없이
# JSON으로 직렬화한다.
def hybrid_descriptor_payload(descriptor: HybridDescriptor) -> JsonObject:
    """Render a shared hybrid descriptor without display-only prose."""
    return {
        "candidate_id": str(descriptor.candidate_id),
        "concept_card_ids": list(descriptor.concept_card_ids),
        "concept_family": descriptor.concept_family,
        "evidence_flags": list(descriptor.evidence_flags),
        "export_citations": [
            export_citation_payload(citation)
            for citation in descriptor.export_citations
        ],
        "provenance_strength": descriptor.provenance_strength,
        "qwen_extracted_descriptors": list(descriptor.qwen_extracted_descriptors),
        "qwen_selected_terms": list(descriptor.qwen_selected_terms),
        "source_cue_ids": list(descriptor.source_cue_ids),
        "visual_descriptor_tokens": list(descriptor.visual_descriptor_tokens),
    }


# hybrid_descriptor_payload에서 export_citations 목록을 직렬화할 때 호출된다.
def export_citation_payload(citation: ExportCitationBridge) -> JsonObject:
    """Render one export citation bridge record."""
    return {"citation_id": citation.citation_id, "status": citation.status}


# relation_outcome_payload에서 호출된다. 관계 판정에 실제로 쓰인 구조화 입력
# 필드들을 직렬화한다.
def relation_input_payload(relation_input: RelationAuthorityInput) -> JsonObject:
    """Render the shared relation-authority input fields used for a decision."""
    return {
        "candidate_id": str(relation_input.candidate_id),
        "citation_provenance_strength": (relation_input.citation_provenance_strength),
        "concept_family_compatible": relation_input.concept_family_compatible,
        "descriptor_compatible": relation_input.descriptor_compatible,
        "duplicate_suppression_key": relation_input.duplicate_suppression_key,
        "evidence_flags": list(relation_input.evidence_flags),
        "geometry_metric_ids": list(relation_input.geometry_metric_ids),
        "rag_status": relation_input.rag_status.value,
        "same_object_ids": list(relation_input.same_object_ids),
        "source_view_ids": list(relation_input.source_view_ids),
    }


# anomaly_grouping/io.py의 _optional_hybrid_descriptor가 호출한다. runner JSON의
# hybrid_descriptor 객체를 공유 타입으로 파싱한다.
def parse_hybrid_descriptor(payload: JsonObject) -> HybridDescriptor:
    """Parse one shared hybrid descriptor from runner JSON."""
    return HybridDescriptor(
        candidate_id=CandidateId(_string(payload, "candidate_id")),
        concept_family=_string(payload, "concept_family"),
        visual_descriptor_tokens=_strings(payload, "visual_descriptor_tokens"),
        qwen_selected_terms=_strings(payload, "qwen_selected_terms"),
        qwen_extracted_descriptors=_strings(
            payload,
            "qwen_extracted_descriptors",
        ),
        concept_card_ids=_strings(payload, "concept_card_ids"),
        export_citations=tuple(
            _export_citation(item) for item in _objects(payload, "export_citations")
        ),
        source_cue_ids=_strings(payload, "source_cue_ids"),
        provenance_strength=_string(payload, "provenance_strength"),
        evidence_flags=_strings(payload, "evidence_flags"),
    )


# anomaly_grouping/io.py의 _optional_relation_input이 호출한다. 이미 파싱된
# descriptor와 함께 relation_authority_input 객체를 공유 타입으로 파싱한다.
def parse_relation_authority_input(
    payload: JsonObject,
    descriptor: HybridDescriptor,
) -> RelationAuthorityInput:
    """Parse one shared relation-authority input from runner JSON."""
    return RelationAuthorityInput(
        candidate_id=CandidateId(_string(payload, "candidate_id")),
        hybrid_descriptor=descriptor,
        geometry_metric_ids=_strings(payload, "geometry_metric_ids"),
        same_object_ids=_strings(payload, "same_object_ids"),
        source_view_ids=_strings(payload, "source_view_ids"),
        duplicate_suppression_key=_string(payload, "duplicate_suppression_key"),
        concept_family_compatible=_bool(payload, "concept_family_compatible"),
        descriptor_compatible=_bool(payload, "descriptor_compatible"),
        citation_provenance_strength=_string(
            payload,
            "citation_provenance_strength",
        ),
        rag_status=_rag_status(payload),
        evidence_flags=_strings(payload, "evidence_flags"),
    )


# parse_hybrid_descriptor에서 export_citations 배열의 항목마다 호출된다.
def _export_citation(payload: JsonObject) -> ExportCitationBridge:
    return ExportCitationBridge(
        citation_id=_string(payload, "citation_id"),
        status=_string(payload, "status"),
    )


def _objects(payload: JsonObject, field_name: str) -> tuple[JsonObject, ...]:
    value = payload.get(field_name)
    if not isinstance(value, list):
        _raise_contract(field_name, "must be a list of objects")
    objects: list[JsonObject] = []
    for item in value:
        if not isinstance(item, dict):
            _raise_contract(field_name, "must be a list of objects")
        objects.append(item)
    return tuple(objects)


def _string(payload: JsonObject, field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str):
        _raise_contract(field_name, "must be a string")
    return value


def _strings(payload: JsonObject, field_name: str) -> tuple[str, ...]:
    value = payload.get(field_name)
    if not isinstance(value, list):
        _raise_contract(field_name, "must be a list of strings")
    values: list[str] = []
    for item in value:
        if not isinstance(item, str):
            _raise_contract(field_name, "must be a list of strings")
        values.append(item)
    return tuple(values)


def _bool(payload: JsonObject, field_name: str) -> bool:
    value = payload.get(field_name)
    if not isinstance(value, bool):
        _raise_contract(field_name, "must be a boolean")
    return value


def _rag_status(payload: JsonObject) -> RagAccountingStatus:
    raw_status = _string(payload, "rag_status")
    try:
        return RagAccountingStatus(raw_status)
    except ValueError as error:
        field_name = "rag_status"
        raise ContractValidationError(field_name, raw_status) from error


def _raise_contract(field_name: str, reason: str) -> NoReturn:
    raise ContractValidationError(field_name, reason)
