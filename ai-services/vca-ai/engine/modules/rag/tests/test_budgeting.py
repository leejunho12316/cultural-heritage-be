from __future__ import annotations

from pathlib import Path

import pytest

from modules.rag.operations.budgeting import RagBudgetInputs, build_rag_budget_inputs
from modules.shared import (
    BUDGET_THRESHOLDS,
    BudgetCounts,
    CandidateId,
    ContractValidationError,
    FollowupMode,
    QwenBridgeStatus,
    RagAccountingRow,
    RagAccountingStatus,
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


def test_budget_inputs_reject_non_terminal_accounting_rows() -> None:
    # Given: a non-terminal accounting row.
    # When/Then: final budget sidecar inputs cannot carry it.
    with pytest.raises(ContractValidationError, match="terminal"):
        _ = build_rag_budget_inputs(
            planned_counts=BUDGET_THRESHOLDS.within_limits_counts(),
            accounting_rows=(_non_terminal_row(),),
        )


def test_rag_budgeting_source_has_no_approval_artifact_ownership() -> None:
    # Given: RAG budgeting source code.
    source = Path("modules/rag/operations/budgeting.py").read_text(encoding="utf-8")

    # When/Then: approval artifact construction and matching remain absent.
    forbidden = (
        "make_budget_approval_request",
        "user_budget_approval",
        "budget_approval_matches_request",
        "budget_approval_request.json",
        "budget_approval.json",
    )
    assert all(value not in source for value in forbidden)
