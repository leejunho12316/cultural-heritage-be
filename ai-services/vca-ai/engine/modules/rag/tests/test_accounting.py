from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields

import pytest

from modules.rag.operations.accounting import (
    RagQueryEvidence,
    account_target,
    attempt_created_row,
    budget_blocked_row,
    completed_row,
    failed_no_citation_row,
    failed_no_visual_cue_row,
    failed_qwen_unavailable_row,
    invalid_parent_row,
    reopen_blocked_row,
    reopen_completed_row,
    reopen_created_row,
    reopen_forbidden_final_row,
    reopen_required_row,
    reopen_skipped_row,
    require_terminal_accounting,
    same_anomaly_suppressed_row,
)
from modules.rag.operations.targets import (
    CoverageMetric,
    RagTarget,
    SelectedParentTarget,
    TargetResolution,
    aggregate_targets,
    automatic_target,
    user_requested_target,
)
from modules.shared import (
    USER_FOLLOWUP_REQUEST_SCHEMA_VERSION,
    CandidateId,
    FollowupMode,
    FollowupSelectorType,
    QwenBridgeStatus,
    RagAccountingRow,
    RagAccountingStatus,
    UserFollowupRequest,
)

type ReasonedRowConstructor = Callable[
    [RagTarget, RagQueryEvidence, str],
    RagAccountingRow,
]
type TransitionalRowConstructor = Callable[
    [RagTarget, RagQueryEvidence],
    RagAccountingRow,
]


def _resolution(*, parent_valid: bool = True) -> TargetResolution:
    return TargetResolution(
        candidate_id=CandidateId("candidate-001"),
        selected_parent=SelectedParentTarget(
            selector_type=FollowupSelectorType.CANDIDATE_ID,
            selector_id="candidate-001",
            valid=parent_valid,
        ),
        rag_parent_candidate_id=CandidateId("candidate-001"),
        same_anomaly_group_id="same-anomaly-001",
        source_candidate_ids=(
            CandidateId("candidate-001"),
            CandidateId("candidate-002"),
        ),
        coverage_metrics=(
            CoverageMetric("citation_coverage", 0.75),
            CoverageMetric("visual_cue_coverage", 0.5),
        ),
        duplicate_suppression_key="candidate-001:surface-change",
        skip_reason="parent selected for grouped refinement",
    )


def _request() -> UserFollowupRequest:
    return UserFollowupRequest(
        schema_version=USER_FOLLOWUP_REQUEST_SCHEMA_VERSION,
        request_id="request-001",
        selector_type=FollowupSelectorType.CANDIDATE_ID,
        selector_id="candidate-001",
        followup_reason="review localized surface change",
        trigger_priority=12,
        requester="reviewer",
        requested_at="2026-08-02T00:00:00Z",
        dry_run_only=False,
    )


def _automatic_target() -> RagTarget:
    return automatic_target(
        _resolution(),
        followup_reason="visual_cue_detected",
        trigger_priority=8,
    )


def _success_evidence() -> RagQueryEvidence:
    return RagQueryEvidence(
        qwen_status=QwenBridgeStatus.SUCCESS,
        rag_query_terms=("brown region",),
        rag_query_descriptors=("irregular",),
    )


def _failed_evidence() -> RagQueryEvidence:
    return RagQueryEvidence(
        qwen_status=QwenBridgeStatus.FAILED,
        rag_query_terms=(),
        rag_query_descriptors=(),
        failure_reason="qwen_backend_unavailable",
    )


def test_automatic_and_user_requested_targets_share_schema_and_metadata() -> None:
    # Given: automatic metadata and a validated shared user follow-up request.
    automatic = _automatic_target()
    requested = user_requested_target(_request(), _resolution())

    # When: both origins are aggregated into the common target stream.
    targets = aggregate_targets((automatic,), (requested,))

    # Then: only origin/reason/priority differ while required metadata is preserved.
    assert type(targets[0]) is type(targets[1])
    assert {field.name for field in fields(type(targets[0]))} >= {
        "selected_parent",
        "duplicate_suppression_key",
        "source_candidate_ids",
        "coverage_metrics",
        "skip_reason",
    }
    assert tuple(target.followup_mode for target in targets) == (
        FollowupMode.AUTOMATIC,
        FollowupMode.USER_REQUESTED,
    )
    assert requested.followup_reason == "review localized surface change"
    assert requested.trigger_priority == 12
    assert requested.selected_parent == automatic.selected_parent
    assert requested.source_candidate_ids == automatic.source_candidate_ids
    assert requested.coverage_metrics == automatic.coverage_metrics
    assert requested.duplicate_suppression_key == automatic.duplicate_suppression_key
    assert requested.skip_reason == automatic.skip_reason


def test_every_target_can_be_paired_with_one_terminal_shared_row() -> None:
    # Given: valid automatic and user-requested targets using the same schema.
    targets = (
        _automatic_target(),
        user_requested_target(_request(), _resolution()),
    )
    evidence = _success_evidence()

    # When: each target is paired with its terminal outcome.
    records = (
        account_target(targets[0], completed_row(targets[0], evidence)),
        account_target(
            targets[1],
            failed_no_citation_row(targets[1], evidence, "no corpus citation"),
        ),
    )
    terminal_records = require_terminal_accounting(targets, records)

    # Then: every record contains the shared row and preserves its complete target.
    assert all(isinstance(record.row, RagAccountingRow) for record in terminal_records)
    assert tuple(record.target for record in terminal_records) == targets
    assert all(record.row.terminal for record in terminal_records)


def test_invalid_parent_has_explicit_terminal_status() -> None:
    # Given: a validated request whose selected parent cannot be resolved.
    target = user_requested_target(_request(), _resolution(parent_valid=False))

    # When: accounting closes the unresolved target.
    row = invalid_parent_row(target, _failed_evidence(), "parent candidate missing")

    # Then: invalid-parent failure is explicit rather than silently omitted.
    assert row.status is RagAccountingStatus.FAILED_INVALID_PARENT_TARGET
    assert row.terminal is True
    assert row.followup_mode is FollowupMode.USER_REQUESTED


@pytest.mark.parametrize(
    ("constructor", "expected_status"),
    [
        (failed_no_citation_row, RagAccountingStatus.FAILED_NO_CITATION),
        (failed_no_visual_cue_row, RagAccountingStatus.FAILED_NO_VISUAL_CUE),
        (budget_blocked_row, RagAccountingStatus.BLOCKED_BY_BUDGET_APPROVAL),
        (
            same_anomaly_suppressed_row,
            RagAccountingStatus.SKIPPED_SUPPRESSED_WITH_PARENT,
        ),
    ],
)
def test_terminal_failure_helpers_create_shared_rows(
    constructor: ReasonedRowConstructor,
    expected_status: RagAccountingStatus,
) -> None:
    # Given: a valid target and successful query-driving evidence.
    target = _automatic_target()

    # When: a terminal non-completion outcome is recorded.
    row = constructor(target, _success_evidence(), "terminal outcome reason")

    # Then: the helper emits the selected shared terminal status.
    assert isinstance(row, RagAccountingRow)
    assert row.status is expected_status
    assert row.terminal is True
    assert row.failure_reason == "terminal outcome reason"


def test_failed_qwen_row_has_no_query_terms_and_keeps_failure_reason() -> None:
    # Given: failed Qwen evidence with no query-driving fields.
    target = _automatic_target()

    # When: RAG records the required failed-Qwen terminal row.
    row = failed_qwen_unavailable_row(target, _failed_evidence())

    # Then: no Qwen terms are invented and the failure remains accountable.
    assert row.status is RagAccountingStatus.FAILED_QWEN_UNAVAILABLE
    assert row.qwen_status is QwenBridgeStatus.FAILED
    assert row.rag_query_terms == ()
    assert row.rag_query_descriptors == ()
    assert row.failure_reason == "qwen_backend_unavailable"
    assert row.terminal is True


@pytest.mark.parametrize(
    ("constructor", "expected_status"),
    [
        (reopen_skipped_row, RagAccountingStatus.REOPEN_SKIPPED),
        (reopen_blocked_row, RagAccountingStatus.REOPEN_BLOCKED),
        (reopen_forbidden_final_row, RagAccountingStatus.REOPEN_FORBIDDEN_FINAL),
    ],
)
def test_reopened_terminal_failure_outcomes_are_reachable(
    constructor: ReasonedRowConstructor,
    expected_status: RagAccountingStatus,
) -> None:
    # Given: a reopened target and explicit failed evidence context.
    target = _automatic_target()

    # When: a terminal reopened failure outcome is created.
    row = constructor(target, _failed_evidence(), "reopen outcome reason")

    # Then: the shared row is terminal with the exact reopened status.
    assert row.status is expected_status
    assert row.terminal is True


def test_reopen_completed_is_terminal() -> None:
    # Given: a reopened target with query-driving evidence.
    target = _automatic_target()

    # When: reopened retrieval completes.
    row = reopen_completed_row(target, _success_evidence())

    # Then: completion is represented by the shared terminal reopened status.
    assert row.status is RagAccountingStatus.REOPEN_COMPLETED
    assert row.terminal is True


@pytest.mark.parametrize(
    ("constructor", "expected_status"),
    [
        (attempt_created_row, RagAccountingStatus.ATTEMPT_CREATED),
        (reopen_required_row, RagAccountingStatus.REOPEN_REQUIRED),
        (reopen_created_row, RagAccountingStatus.REOPEN_CREATED),
    ],
)
def test_non_terminal_rows_are_identifiable_but_not_completion(
    constructor: TransitionalRowConstructor,
    expected_status: RagAccountingStatus,
) -> None:
    # Given: a target with valid query-driving evidence.
    target = _automatic_target()

    # When: a transitional row is created.
    row = constructor(target, _success_evidence())

    # Then: it is identifiable but cannot masquerade as terminal completion.
    assert row.status is expected_status
    assert row.status is not RagAccountingStatus.COMPLETED
    assert row.terminal is False
