from __future__ import annotations

import pytest

from modules.shared import (
    ContractValidationError,
    ExitCode,
    FinalSuccessInput,
    RunStatus,
    evaluate_final_success,
)


def test_dry_run_exits_ok_with_pre_qwen_preview_status() -> None:
    # Given: an explicit dry run, before any real stage executes.
    result = evaluate_final_success(
        FinalSuccessInput(
            dry_run=True,
            failed_stage=None,
            rough_masking_blocked=False,
            accepted_candidate_count=0,
        )
    )

    # Then: the incomplete dry-run preview remains a successful process command.
    assert result.status is RunStatus.INCOMPLETE_PRE_QWEN_PREVIEW
    assert result.final_success is False
    assert result.exit_code is ExitCode.OK


def test_rough_masking_budget_block_takes_priority_over_the_failed_stage() -> None:
    # Given: rough_masking failed because its tile budget gate is unapproved.
    result = evaluate_final_success(
        FinalSuccessInput(
            dry_run=False,
            failed_stage="rough_masking",
            rough_masking_blocked=True,
            accepted_candidate_count=0,
        )
    )

    # Then: the run is reported as blocked, not a generic failure.
    assert result.status is RunStatus.BLOCKED
    assert result.final_success is False
    assert result.exit_code is ExitCode.INCOMPLETE_OR_FAILURE


def test_report_generating_only_failure_is_incomplete_not_failure() -> None:
    # Given: every upstream stage completed but report_generating's own
    # verification failed.
    result = evaluate_final_success(
        FinalSuccessInput(
            dry_run=False,
            failed_stage="report_generating",
            rough_masking_blocked=False,
            accepted_candidate_count=3,
        )
    )

    # Then: real candidate data exists, so this is incomplete, not a hard failure.
    assert result.status is RunStatus.INCOMPLETE
    assert result.final_success is False
    assert result.exit_code is ExitCode.INCOMPLETE_OR_FAILURE


def test_any_other_stage_failure_is_reported_as_failure() -> None:
    # Given: a stage other than rough_masking (blocked) or report_generating crashed.
    result = evaluate_final_success(
        FinalSuccessInput(
            dry_run=False,
            failed_stage="mask_refining",
            rough_masking_blocked=False,
            accepted_candidate_count=0,
        )
    )

    # Then: the run is a hard failure.
    assert result.status is RunStatus.FAILURE
    assert result.final_success is False
    assert result.exit_code is ExitCode.INCOMPLETE_OR_FAILURE


def test_all_stages_complete_with_zero_kept_candidates_is_no_valid_targets() -> None:
    # Given: every stage completed cleanly but no candidate survived to the end.
    result = evaluate_final_success(
        FinalSuccessInput(
            dry_run=False,
            failed_stage=None,
            rough_masking_blocked=False,
            accepted_candidate_count=0,
        )
    )

    # Then: this is a clean run that simply found nothing, not a failure.
    assert result.status is RunStatus.NO_VALID_ROUGH_TARGETS
    assert result.final_success is False
    assert result.exit_code is ExitCode.INCOMPLETE_OR_FAILURE


def test_all_stages_complete_with_kept_candidates_is_success() -> None:
    # Given: every stage completed and at least one candidate was kept.
    result = evaluate_final_success(
        FinalSuccessInput(
            dry_run=False,
            failed_stage=None,
            rough_masking_blocked=False,
            accepted_candidate_count=2,
        )
    )

    # Then: the run is a full success.
    assert result.status is RunStatus.SUCCESS
    assert result.final_success is True
    assert result.exit_code is ExitCode.OK


def test_negative_accepted_candidate_count_is_rejected() -> None:
    # Given: an impossible negative candidate count.
    # When/Then: the shared boundary rejects it before any status is chosen.
    with pytest.raises(ContractValidationError):
        _ = evaluate_final_success(
            FinalSuccessInput(
                dry_run=False,
                failed_stage=None,
                rough_masking_blocked=False,
                accepted_candidate_count=-1,
            )
        )
