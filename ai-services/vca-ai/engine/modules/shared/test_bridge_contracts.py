from __future__ import annotations

from dataclasses import fields

import pytest

from modules.shared import (
    BRIDGE_SCHEMA_VERSION,
    BridgeFieldRole,
    BridgeFieldUsage,
    CandidateId,
    ContractValidationError,
    ExportCitationBridge,
    FollowupMode,
    HybridDescriptor,
    QwenBridgeResult,
    QwenBridgeStatus,
    RagAccountingRow,
    RagAccountingStatus,
    RelationAuthorityInput,
    field_usage,
    is_terminal_rag_status,
    terminal_rag_statuses,
)


def _qwen_success() -> QwenBridgeResult:
    return QwenBridgeResult(
        candidate_id=CandidateId("candidate-001"),
        status=QwenBridgeStatus.SUCCESS,
        selected_terms=("brown region",),
        extracted_descriptors=("irregular", "matte"),
        confidence=0.84,
        reason="visual descriptors match both Qwen input views",
        qwen_observation_id="qwen-observation-001",
        input_view_hashes=("a" * 64, "b" * 64),
    )


def _qwen_failed() -> QwenBridgeResult:
    return QwenBridgeResult(
        candidate_id=CandidateId("candidate-002"),
        status=QwenBridgeStatus.FAILED,
        selected_terms=(),
        extracted_descriptors=(),
        confidence=None,
        reason="malformed_qwen_json",
        qwen_observation_id=None,
        input_view_hashes=(),
        failure_code="malformed_output",
    )


def test_qwen_field_roles_separate_query_drivers_from_provenance() -> None:
    # Given: a successful Qwen bridge result and the shared role map.
    result = _qwen_success()

    # When: C-004 field usage is inspected.
    query_driving = tuple(
        role
        for role in BridgeFieldRole
        if field_usage(role) is BridgeFieldUsage.QUERY_DRIVING
    )

    # Then: only selected terms and extracted descriptors may drive RAG queries.
    assert query_driving == (
        BridgeFieldRole.SELECTED_TERMS,
        BridgeFieldRole.EXTRACTED_DESCRIPTORS,
    )
    assert result.selected_terms == ("brown region",)
    assert result.extracted_descriptors == ("irregular", "matte")


def test_qwen_observation_metadata_is_provenance_only() -> None:
    # Given: Qwen observation metadata retained for accounting and ranking.
    result = _qwen_success()
    provenance_roles = (
        BridgeFieldRole.CONFIDENCE,
        BridgeFieldRole.REASON,
        BridgeFieldRole.QWEN_OBSERVATION_ID,
        BridgeFieldRole.INPUT_VIEW_HASHES,
    )

    # When: each metadata field is classified by the shared contract.
    usages = tuple(field_usage(role) for role in provenance_roles)

    # Then: none of these fields may become retrieval query drivers.
    assert usages == (BridgeFieldUsage.PROVENANCE_ONLY,) * len(provenance_roles)
    assert result.confidence == 0.84
    assert result.reason
    assert result.qwen_observation_id == "qwen-observation-001"
    assert result.input_view_hashes == ("a" * 64, "b" * 64)


def test_failed_qwen_candidate_requires_terminal_rag_accounting_row() -> None:
    # Given: a valid detector candidate with a failed non-null Qwen bridge result.
    failed = _qwen_failed()

    # When: RAG records terminal accounting for that candidate.
    row = RagAccountingRow(
        candidate_id=failed.candidate_id,
        followup_mode=FollowupMode.AUTOMATIC,
        status=RagAccountingStatus.FAILED_QWEN_UNAVAILABLE,
        qwen_status=failed.status,
        rag_query_terms=(),
        rag_query_descriptors=(),
        failure_reason=failed.failure_code,
    )

    # Then: failed Qwen output is referenced explicitly, never silently skipped.
    assert row.candidate_id == failed.candidate_id
    assert row.qwen_status is QwenBridgeStatus.FAILED
    assert row.terminal is True
    assert is_terminal_rag_status(row.status) is True


def test_hybrid_descriptor_is_visual_evidence_centered() -> None:
    # Given: the structured bridge descriptor consumed by RAG/relation authority.
    descriptor = HybridDescriptor(
        candidate_id=CandidateId("candidate-001"),
        concept_family="surface_change",
        visual_descriptor_tokens=("brown", "irregular"),
        qwen_selected_terms=("brown region",),
        qwen_extracted_descriptors=("irregular",),
        concept_card_ids=("concept-001",),
        export_citations=(ExportCitationBridge("citation-001", "included"),),
        source_cue_ids=("cue-001",),
        provenance_strength="strong",
        evidence_flags=("qwen_success", "rag_cited"),
    )

    # When: the descriptor schema is inspected.
    descriptor_fields = {field.name for field in fields(HybridDescriptor)}

    # Then: it contains visual evidence fields and no diagnosis-like prose fields.
    forbidden = {
        "summary",
        "natural_language_summary",
        "diagnosis",
        "treatment",
        "severity",
        "urgency",
        "repair",
        "restoration",
        "preservation",
    }
    assert descriptor.concept_family == "surface_change"
    assert descriptor_fields.isdisjoint(forbidden)


def test_terminal_rag_status_is_explicit_for_rows_and_relation_inputs() -> None:
    # Given: every shared RAG accounting status and a hybrid descriptor.
    descriptor = HybridDescriptor(
        candidate_id=CandidateId("candidate-001"),
        concept_family="surface_change",
        visual_descriptor_tokens=("brown",),
        qwen_selected_terms=("brown region",),
        qwen_extracted_descriptors=("irregular",),
        concept_card_ids=("concept-001",),
        export_citations=(ExportCitationBridge("citation-001", "included"),),
        source_cue_ids=("cue-001",),
        provenance_strength="strong",
        evidence_flags=("qwen_success",),
    )

    # When: terminal statuses and relation-authority input are validated.
    terminal = terminal_rag_statuses()
    relation_input = RelationAuthorityInput(
        candidate_id=CandidateId("candidate-001"),
        hybrid_descriptor=descriptor,
        geometry_metric_ids=("geometry-001",),
        same_object_ids=("object-001",),
        source_view_ids=("view-001",),
        duplicate_suppression_key="candidate-001:surface_change",
        concept_family_compatible=True,
        descriptor_compatible=True,
        citation_provenance_strength="strong",
        rag_status=RagAccountingStatus.COMPLETED,
        evidence_flags=("structured_only",),
    )

    # Then: non-terminal statuses are rejected from terminal rows/relations.
    assert BRIDGE_SCHEMA_VERSION == "qwen-rag-bridge-v1"
    assert RagAccountingStatus.REOPEN_REQUIRED not in terminal
    assert RagAccountingStatus.ATTEMPT_CREATED not in terminal
    assert RagAccountingStatus.REOPEN_CREATED not in terminal
    assert is_terminal_rag_status(RagAccountingStatus.REOPEN_REQUIRED) is False
    assert relation_input.rag_status is RagAccountingStatus.COMPLETED
    reopen_row = RagAccountingRow(
        candidate_id=CandidateId("candidate-001"),
        followup_mode=FollowupMode.AUTOMATIC,
        status=RagAccountingStatus.REOPEN_REQUIRED,
        qwen_status=QwenBridgeStatus.SUCCESS,
        rag_query_terms=("brown region",),
        rag_query_descriptors=("irregular",),
    )
    assert reopen_row.terminal is False
    with pytest.raises(ContractValidationError):
        _ = RelationAuthorityInput(
            candidate_id=CandidateId("candidate-001"),
            hybrid_descriptor=descriptor,
            geometry_metric_ids=("geometry-001",),
            same_object_ids=("object-001",),
            source_view_ids=("view-001",),
            duplicate_suppression_key="candidate-001:surface_change",
            concept_family_compatible=True,
            descriptor_compatible=True,
            citation_provenance_strength="strong",
            rag_status=RagAccountingStatus.REOPEN_REQUIRED,
            evidence_flags=("structured_only",),
        )


def test_bridge_result_exposes_only_query_driving_values_to_rag() -> None:
    # Given: a Qwen bridge result with both query and provenance fields.
    result = _qwen_success()

    # When: RAG asks for the allowed query-driving seam.
    terms, descriptors = result.query_driving_fields()

    # Then: provenance fields are not reachable through that seam.
    assert terms == ("brown region",)
    assert descriptors == ("irregular", "matte")
    assert result.reason not in terms
    assert result.input_view_hashes != terms


def test_qwen_status_and_rag_accounting_status_must_be_consistent() -> None:
    # Given: impossible Qwen/RAG accounting combinations.
    failed = _qwen_failed()

    # When/Then: Qwen failure cannot be marked completed or omit failure reason.
    with pytest.raises(ContractValidationError):
        _ = RagAccountingRow(
            candidate_id=failed.candidate_id,
            followup_mode=FollowupMode.AUTOMATIC,
            status=RagAccountingStatus.COMPLETED,
            qwen_status=QwenBridgeStatus.FAILED,
            rag_query_terms=(),
            rag_query_descriptors=(),
            failure_reason=failed.failure_code,
        )
    with pytest.raises(ContractValidationError):
        _ = RagAccountingRow(
            candidate_id=failed.candidate_id,
            followup_mode=FollowupMode.AUTOMATIC,
            status=RagAccountingStatus.FAILED_QWEN_UNAVAILABLE,
            qwen_status=QwenBridgeStatus.FAILED,
            rag_query_terms=(),
            rag_query_descriptors=(),
        )
    with pytest.raises(ContractValidationError):
        _ = RagAccountingRow(
            candidate_id=CandidateId("candidate-003"),
            followup_mode=FollowupMode.AUTOMATIC,
            status=RagAccountingStatus.FAILED_QWEN_UNAVAILABLE,
            qwen_status=QwenBridgeStatus.SUCCESS,
            rag_query_terms=("brown region",),
            rag_query_descriptors=("irregular",),
        )


def test_bridge_field_roles_match_qwen_bridge_result_fields() -> None:
    # Given: field roles that must track the Qwen bridge result schema.
    result_fields = {field.name for field in fields(QwenBridgeResult)}

    # When/Then: every role value remains a real QwenBridgeResult field.
    assert {role.value for role in BridgeFieldRole} <= result_fields
    assert "morphology" not in result_fields
