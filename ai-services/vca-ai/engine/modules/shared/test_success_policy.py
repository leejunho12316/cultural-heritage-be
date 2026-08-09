from __future__ import annotations

import pytest

from modules.shared import (
    ContractValidationError,
    DetectorLane,
    ExitCode,
    FinalSuccessInput,
    LaneExecutionReceipt,
    LaneExecutionStatus,
    RunStatus,
    evaluate_final_success,
)


def test_real_execution_with_zero_accepted_candidates_is_not_success() -> None:
    # Given: all requested detector lanes ran but produced no accepted rough target.
    lanes = (
        LaneExecutionReceipt(
            lane=DetectorLane.OWLV2_SAM2, status=LaneExecutionStatus.REAL_EXECUTED
        ),
    )

    # When: final-success policy evaluates the real execution.
    result = evaluate_final_success(
        FinalSuccessInput(
            real_execution=True,
            dry_run=False,
            requested_detector_lanes=(DetectorLane.OWLV2_SAM2,),
            lane_receipts=lanes,
            accepted_candidate_count=0,
            accepted_candidate_final_successes=(),
        )
    )

    # Then: command success alone cannot disguise the missing rough target.
    assert result.status is RunStatus.NO_VALID_ROUGH_TARGETS
    assert result.final_success is False
    assert result.exit_code is ExitCode.INCOMPLETE_OR_FAILURE


def test_requested_lane_that_is_not_real_executed_blocks_final_success() -> None:
    # Given: a requested lane was skipped despite an otherwise final candidate.
    lanes = (
        LaneExecutionReceipt(
            lane=DetectorLane.OWLV2_SAM2, status=LaneExecutionStatus.REAL_EXECUTED
        ),
        LaneExecutionReceipt(
            lane=DetectorLane.FLORENCE2_SAM2,
            status=LaneExecutionStatus.SKIPPED_NOT_REQUESTED,
        ),
    )

    # When: final-success policy evaluates both requested lanes.
    result = evaluate_final_success(
        FinalSuccessInput(
            real_execution=True,
            dry_run=False,
            requested_detector_lanes=(
                DetectorLane.OWLV2_SAM2,
                DetectorLane.FLORENCE2_SAM2,
            ),
            lane_receipts=lanes,
            accepted_candidate_count=1,
            accepted_candidate_final_successes=(True,),
        )
    )

    # Then: a non-real lane prevents the misleading success status.
    assert result.status is RunStatus.INCOMPLETE
    assert result.final_success is False
    assert result.exit_code is ExitCode.INCOMPLETE_OR_FAILURE


def test_all_real_requested_lanes_and_final_candidates_produce_success() -> None:
    # Given: every requested lane and accepted candidate completed successfully.
    lanes = (
        LaneExecutionReceipt(
            lane=DetectorLane.OWLV2_SAM2, status=LaneExecutionStatus.REAL_EXECUTED
        ),
        LaneExecutionReceipt(
            lane=DetectorLane.FLORENCE2_SAM2, status=LaneExecutionStatus.REAL_EXECUTED
        ),
    )

    # When: final-success policy evaluates the completed real execution.
    result = evaluate_final_success(
        FinalSuccessInput(
            real_execution=True,
            dry_run=False,
            requested_detector_lanes=(
                DetectorLane.OWLV2_SAM2,
                DetectorLane.FLORENCE2_SAM2,
            ),
            lane_receipts=lanes,
            accepted_candidate_count=2,
            accepted_candidate_final_successes=(True, True),
        )
    )

    # Then: the value-level outcome is successful with an OK exit code.
    assert result.status is RunStatus.SUCCESS
    assert result.final_success is True
    assert result.exit_code is ExitCode.OK


def test_real_execution_without_requested_lanes_is_rejected() -> None:
    # Given: a real execution declares no requested detector lanes.
    # When: final-success policy validates that impossible execution contract.
    with pytest.raises(ContractValidationError):
        _ = evaluate_final_success(
            FinalSuccessInput(
                real_execution=True,
                dry_run=False,
                requested_detector_lanes=(),
                lane_receipts=(),
                accepted_candidate_count=1,
                accepted_candidate_final_successes=(True,),
            )
        )

    # Then: a vacuous all-lanes check cannot manufacture pipeline success.


def test_non_dry_run_pre_qwen_preview_exits_incomplete_or_failure() -> None:
    # Given: a non-dry-run execution stops before Qwen evidence is complete.
    # When: final-success policy evaluates the preview-only execution.
    result = evaluate_final_success(
        FinalSuccessInput(
            real_execution=False,
            dry_run=False,
            requested_detector_lanes=(DetectorLane.OWLV2_SAM2,),
            lane_receipts=(),
            accepted_candidate_count=0,
            accepted_candidate_final_successes=(),
        )
    )

    # Then: only dry runs may use exit 0 for an incomplete preview status.
    assert result.status is RunStatus.INCOMPLETE_PRE_QWEN_PREVIEW
    assert result.final_success is False
    assert result.exit_code is ExitCode.INCOMPLETE_OR_FAILURE


def test_dry_run_pre_qwen_preview_exits_ok() -> None:
    # Given: an explicit dry run stops before model evidence.
    # When: final-success policy evaluates the dry-run preview.
    result = evaluate_final_success(
        FinalSuccessInput(
            real_execution=False,
            dry_run=True,
            requested_detector_lanes=(DetectorLane.OWLV2_SAM2,),
            lane_receipts=(),
            accepted_candidate_count=0,
            accepted_candidate_final_successes=(),
        )
    )

    # Then: the incomplete dry-run preview remains a successful process command.
    assert result.status is RunStatus.INCOMPLETE_PRE_QWEN_PREVIEW
    assert result.final_success is False
    assert result.exit_code is ExitCode.OK


def test_non_real_requested_lane_takes_precedence_over_zero_candidates() -> None:
    # Given: the missing accepted candidates are caused by a requested lane failure.
    lanes = (
        LaneExecutionReceipt(
            lane=DetectorLane.OWLV2_SAM2, status=LaneExecutionStatus.FAILED
        ),
    )

    # When: final-success policy evaluates the real execution.
    result = evaluate_final_success(
        FinalSuccessInput(
            real_execution=True,
            dry_run=False,
            requested_detector_lanes=(DetectorLane.OWLV2_SAM2,),
            lane_receipts=lanes,
            accepted_candidate_count=0,
            accepted_candidate_final_successes=(),
        )
    )

    # Then: the receipt preserves the lane-execution root cause.
    assert result.status is RunStatus.INCOMPLETE
    assert result.final_success is False
    assert result.exit_code is ExitCode.INCOMPLETE_OR_FAILURE
