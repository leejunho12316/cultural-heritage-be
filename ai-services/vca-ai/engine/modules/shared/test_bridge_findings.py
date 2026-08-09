from __future__ import annotations

from dataclasses import replace

import pytest

from modules.shared import (
    CandidateId,
    ContractValidationError,
    ExportCitationBridge,
    FollowupMode,
    HybridDescriptor,
    QwenBridgeResult,
    QwenBridgeStatus,
    QwenRagQueryInput,
    RagAccountingRow,
    RagAccountingStatus,
    RelationAuthorityInput,
    RelationAuthorityOutcome,
    RelationAuthorityOutcomeState,
)


def _descriptor(candidate_id: CandidateId) -> HybridDescriptor:
    return HybridDescriptor(
        candidate_id=candidate_id,
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


@pytest.mark.parametrize(
    "status",
    [
        RagAccountingStatus.SKIPPED_SUPPRESSED_WITH_PARENT,
        RagAccountingStatus.BLOCKED_BY_BUDGET_APPROVAL,
        RagAccountingStatus.FAILED_NO_CITATION,
        RagAccountingStatus.FAILED_NO_VISUAL_CUE,
        RagAccountingStatus.FAILED_INVALID_PARENT_TARGET,
        RagAccountingStatus.FAILED_QWEN_UNAVAILABLE,
        RagAccountingStatus.REOPEN_COMPLETED,
        RagAccountingStatus.REOPEN_SKIPPED,
        RagAccountingStatus.REOPEN_BLOCKED,
        RagAccountingStatus.REOPEN_FORBIDDEN_FINAL,
    ],
)
def test_qwen_failed_rows_accept_terminal_failure_accounting_statuses(
    status: RagAccountingStatus,
) -> None:
    # Given: a valid detector candidate whose Qwen observation failed.
    candidate_id = CandidateId("candidate-qwen-failed")

    # When: RAG records a terminal weak/failure accounting status.
    row = RagAccountingRow(
        candidate_id=candidate_id,
        followup_mode=FollowupMode.AUTOMATIC,
        status=status,
        qwen_status=QwenBridgeStatus.FAILED,
        rag_query_terms=(),
        rag_query_descriptors=(),
        failure_reason="malformed_qwen_json",
    )

    # Then: the failed Qwen candidate still has an explicit terminal row.
    assert row.terminal is True
    assert row.status is status


def test_relation_authority_outcome_preserves_locked_bridge_fields() -> None:
    # Given: terminal structured relation-authority input from the bridge.
    candidate_id = CandidateId("candidate-001")
    descriptor = _descriptor(candidate_id)
    relation_input = RelationAuthorityInput(
        candidate_id=candidate_id,
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

    # When: relation authority emits the shared outcome contract.
    outcome = RelationAuthorityOutcome(
        candidate_id=candidate_id,
        hybrid_descriptor=descriptor,
        relation_authority_input=relation_input,
        relation_authority_outcome=RelationAuthorityOutcomeState.SAME_ANOMALY_REFINEMENT,
        rag_status=RagAccountingStatus.COMPLETED,
        evidence_flags=("structured_only", "relation_terminal"),
    )

    # Then: downstream grouping can preserve the locked structured fields.
    assert outcome.hybrid_descriptor == descriptor
    assert (
        outcome.relation_authority_outcome
        is RelationAuthorityOutcomeState.SAME_ANOMALY_REFINEMENT
    )


def test_relation_authority_outcome_rejects_unknown_relation_state() -> None:
    # Given: a valid typed relation authority outcome.
    candidate_id = CandidateId("candidate-001")
    descriptor = _descriptor(candidate_id)
    relation_input = RelationAuthorityInput(
        candidate_id=candidate_id,
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
    outcome = RelationAuthorityOutcome(
        candidate_id=candidate_id,
        hybrid_descriptor=descriptor,
        relation_authority_input=relation_input,
        relation_authority_outcome=RelationAuthorityOutcomeState.SAME_ANOMALY_DUPLICATE,
        rag_status=RagAccountingStatus.COMPLETED,
        evidence_flags=("structured_only", "relation_terminal"),
    )

    # When/Then: unknown relation states cannot be smuggled into the contract.
    with pytest.raises(ContractValidationError):
        _ = replace(outcome, relation_authority_outcome="unsupported_relation")


def test_relation_authority_outcome_rejects_raw_relation_state_string() -> None:
    # Given: a valid typed relation authority outcome.
    candidate_id = CandidateId("candidate-001")
    descriptor = _descriptor(candidate_id)
    relation_input = RelationAuthorityInput(
        candidate_id=candidate_id,
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
    outcome = RelationAuthorityOutcome(
        candidate_id=candidate_id,
        hybrid_descriptor=descriptor,
        relation_authority_input=relation_input,
        relation_authority_outcome=RelationAuthorityOutcomeState.SAME_ANOMALY_DUPLICATE,
        rag_status=RagAccountingStatus.COMPLETED,
        evidence_flags=("structured_only", "relation_terminal"),
    )

    # When/Then: even matching raw strings cannot bypass enum identity.
    with pytest.raises(ContractValidationError):
        _ = replace(outcome, relation_authority_outcome="same_anomaly_duplicate")


def test_successful_qwen_bridge_requires_query_driving_evidence() -> None:
    # Given: a successful Qwen bridge handoff without query-driving evidence.
    # When/Then: success cannot be represented without terms or descriptors.
    with pytest.raises(ContractValidationError):
        _ = QwenBridgeResult(
            candidate_id=CandidateId("candidate-empty-query"),
            status=QwenBridgeStatus.SUCCESS,
            selected_terms=(),
            extracted_descriptors=(),
            confidence=0.7,
            reason="No visual cue was selected.",
            qwen_observation_id="qwen-observation-empty",
            input_view_hashes=("a" * 64,),
        )


def test_qwen_bridge_exposes_provenance_free_rag_query_input() -> None:
    # Given: a successful Qwen bridge result with query and provenance fields.
    result = QwenBridgeResult(
        candidate_id=CandidateId("candidate-001"),
        status=QwenBridgeStatus.SUCCESS,
        selected_terms=("brown region",),
        extracted_descriptors=("irregular",),
        confidence=0.84,
        reason="Both input views show the same localized visual feature.",
        qwen_observation_id="qwen-observation-001",
        input_view_hashes=("a" * 64,),
    )

    # When: RAG asks for the typed query-only handoff.
    rag_query = result.to_rag_query_input()

    # Then: the handoff contains no Qwen provenance fields.
    assert rag_query == QwenRagQueryInput(
        candidate_id=CandidateId("candidate-001"),
        selected_terms=("brown region",),
        extracted_descriptors=("irregular",),
    )
    assert not hasattr(rag_query, "reason")
    assert not hasattr(rag_query, "qwen_observation_id")


def test_relation_authority_contract_rejects_candidate_drift() -> None:
    # Given: a relation handoff whose nested descriptor belongs to another candidate.
    descriptor = _descriptor(CandidateId("candidate-001"))

    # When/Then: the shared bridge rejects silent provenance drift.
    with pytest.raises(ContractValidationError):
        _ = RelationAuthorityInput(
            candidate_id=CandidateId("candidate-002"),
            hybrid_descriptor=descriptor,
            geometry_metric_ids=("geometry-001",),
            same_object_ids=("object-001",),
            source_view_ids=("view-001",),
            duplicate_suppression_key="candidate-002:surface_change",
            concept_family_compatible=True,
            descriptor_compatible=True,
            citation_provenance_strength="strong",
            rag_status=RagAccountingStatus.COMPLETED,
            evidence_flags=("structured_only",),
        )
