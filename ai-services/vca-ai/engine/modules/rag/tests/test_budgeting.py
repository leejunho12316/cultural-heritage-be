from __future__ import annotations

from pathlib import Path

import pytest

from modules.rag.operations.budgeting import (
    RagBudgetInputs,
    RagReopenInputs,
    ReopenHashContext,
    build_rag_budget_inputs,
    build_rag_reopen_inputs,
    build_reopen_request_hash_input,
)
from modules.shared import (
    BUDGET_THRESHOLDS,
    BudgetCounts,
    CandidateId,
    ContractValidationError,
    DryRunId,
    FollowupHash,
    FollowupMode,
    PlannedCountHash,
    QwenBridgeStatus,
    RagAccountingRow,
    RagAccountingStatus,
    RuntimeMetadata,
    reopen_request_hash,
)


def _runtime_metadata() -> RuntimeMetadata:
    return RuntimeMetadata(
        recorded_at="2026-08-02T00:00:00Z",
        output_dir="runs/current",
        hostname="host-a",
        temporary_dir="workspace-temp/runtime-a",
        process_id=123,
    )


def _terminal_row(candidate_id: str = "candidate-001") -> RagAccountingRow:
    return RagAccountingRow(
        candidate_id=CandidateId(candidate_id),
        followup_mode=FollowupMode.AUTOMATIC,
        status=RagAccountingStatus.COMPLETED,
        qwen_status=QwenBridgeStatus.SUCCESS,
        rag_query_terms=("white deposit",),
        rag_query_descriptors=("powdery",),
    )


def _non_terminal_row() -> RagAccountingRow:
    return RagAccountingRow(
        candidate_id=CandidateId("candidate-pending"),
        followup_mode=FollowupMode.AUTOMATIC,
        status=RagAccountingStatus.ATTEMPT_CREATED,
        qwen_status=QwenBridgeStatus.SUCCESS,
        rag_query_terms=("white deposit",),
        rag_query_descriptors=("powdery",),
    )


def test_budget_inputs_use_shared_counts_thresholds_and_terminal_rows() -> None:
    # Given: planned RAG work that exceeds every shared threshold.
    counts = BUDGET_THRESHOLDS.exceeding_all_counts()

    # When: RAG builds budget input components for orchestration.
    inputs = build_rag_budget_inputs(
        planned_counts=counts,
        accounting_rows=(_terminal_row(),),
    )

    # Then: RAG returns shared contracts and does not truncate or block counts.
    assert isinstance(inputs, RagBudgetInputs)
    assert inputs.planned_counts is counts
    assert isinstance(inputs.planned_counts, BudgetCounts)
    assert {threshold.value for threshold in inputs.exceeded_thresholds} == {
        "model_invocations",
        "sam2_calls",
        "tiles",
        "prompt_variants",
        "estimated_output_gb",
        "smoke_images",
    }
    assert inputs.requires_budget_review is True
    assert inputs.terminal_accounting_rows == (_terminal_row(),)


def test_budget_and_reopen_inputs_reject_non_terminal_accounting_rows() -> None:
    # Given: a non-terminal accounting row.
    # When/Then: final budget sidecar inputs cannot carry it.
    with pytest.raises(ContractValidationError, match="terminal"):
        _ = build_rag_budget_inputs(
            planned_counts=BUDGET_THRESHOLDS.within_limits_counts(),
            accounting_rows=(_non_terminal_row(),),
        )
    with pytest.raises(ContractValidationError, match="terminal"):
        _ = build_rag_reopen_inputs(
            reopened_candidate_ids=(CandidateId("candidate-001"),),
            initial_planned_counts=BUDGET_THRESHOLDS.within_limits_counts(),
            reopen_incremental_counts=BudgetCounts(1, 0, 0, 0, 0, 0),
            reasons=("review requested",),
            accounting_rows=(_non_terminal_row(),),
        )


def test_reopen_inputs_and_hash_input_use_shared_hash_contracts() -> None:
    # Given: reopened candidate scope and incremental planned counts.
    initial = BUDGET_THRESHOLDS.within_limits_counts()
    incremental = BudgetCounts(1, 2, 3, 4, 5, 1)
    reopened_ids = (CandidateId("candidate-b"), CandidateId("candidate-a"))

    # When: RAG builds reopen inputs and the shared hash input.
    inputs = build_rag_reopen_inputs(
        reopened_candidate_ids=reopened_ids,
        initial_planned_counts=initial,
        reopen_incremental_counts=incremental,
        reasons=("manual follow-up",),
        accounting_rows=(_terminal_row(),),
    )
    hash_input = build_reopen_request_hash_input(
        context=ReopenHashContext(
            dry_run_id=DryRunId("dry-run-001"),
            followup_request_hash=FollowupHash("followup-sha"),
            planned_count_hash=PlannedCountHash("planned-sha"),
            threshold_cap_config_version="thresholds-v1",
            runtime_metadata=_runtime_metadata(),
        ),
        reopen_inputs=inputs,
    )

    # Then: combined counts and reopen hash sensitivity are delegated to shared types.
    assert isinstance(inputs, RagReopenInputs)
    assert inputs.combined_planned_counts == BudgetCounts(1, 2, 3, 4, 5, 1)
    assert hash_input.reopened_candidate_ids == reopened_ids
    assert reopen_request_hash(hash_input) != reopen_request_hash(
        build_reopen_request_hash_input(
            context=ReopenHashContext(
                dry_run_id=DryRunId("dry-run-001"),
                followup_request_hash=FollowupHash("followup-sha"),
                planned_count_hash=PlannedCountHash("planned-sha"),
                threshold_cap_config_version="thresholds-v1",
                runtime_metadata=_runtime_metadata(),
            ),
            reopen_inputs=build_rag_reopen_inputs(
                reopened_candidate_ids=(CandidateId("candidate-c"),),
                initial_planned_counts=initial,
                reopen_incremental_counts=incremental,
                reasons=("manual follow-up",),
                accounting_rows=(_terminal_row(),),
            ),
        )
    )


def test_rag_budgeting_source_has_no_approval_artifact_ownership() -> None:
    # Given: RAG budgeting source code.
    source = Path("modules/rag/operations/budgeting.py").read_text(encoding="utf-8")

    # When/Then: approval artifact construction and matching remain absent.
    forbidden = (
        "make_budget_approval_request",
        "user_budget_approval",
        "user_reopen_budget_approval",
        "budget_approval_matches_request",
        "reopen_approval_matches_request",
        "budget_approval_request.json",
        "budget_approval.json",
        "reopen_budget_approval_request.json",
        "reopen_budget_approval.json",
    )
    assert all(value not in source for value in forbidden)
