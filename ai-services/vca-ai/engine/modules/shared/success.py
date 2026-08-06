"""Final-success and exit-code policy for pipeline orchestration."""

from dataclasses import dataclass

from modules.shared.errors import ContractValidationError
from modules.shared.lanes import DetectorLane
from modules.shared.models import (
    ExitCode,
    LaneExecutionReceipt,
    LaneExecutionStatus,
    RunStatus,
)


@dataclass(frozen=True, slots=True)
class FinalSuccessEvaluation:
    """Value-level final status, success flag, and process exit code."""

    status: RunStatus
    final_success: bool
    exit_code: ExitCode


@dataclass(frozen=True, slots=True)
class FinalSuccessInput:
    """Complete shared input needed to evaluate final pipeline success."""

    real_execution: bool
    dry_run: bool
    requested_detector_lanes: tuple[DetectorLane, ...]
    lane_receipts: tuple[LaneExecutionReceipt, ...]
    accepted_candidate_count: int
    accepted_candidate_final_successes: tuple[bool, ...]


def _is_real_executed(receipt: LaneExecutionReceipt) -> bool:
    match receipt.status:
        case LaneExecutionStatus.REAL_EXECUTED:
            return True
        case LaneExecutionStatus.SKIPPED_NOT_REQUESTED:
            return False
        case LaneExecutionStatus.BLOCKED:
            return False
        case LaneExecutionStatus.FAILED:
            return False


def _requested_lanes_real_executed(
    requested_lanes: tuple[DetectorLane, ...],
    receipts: tuple[LaneExecutionReceipt, ...],
) -> bool:
    return all(
        any(
            receipt.lane is requested_lane and _is_real_executed(receipt)
            for receipt in receipts
        )
        for requested_lane in requested_lanes
    )


def evaluate_final_success(value: FinalSuccessInput) -> FinalSuccessEvaluation:
    """Evaluate mandatory requested-lane and accepted-candidate success gates."""
    if value.accepted_candidate_count < 0:
        field = "accepted_candidate_count"
        raise ContractValidationError(field, "must be non-negative")
    if value.accepted_candidate_count != len(value.accepted_candidate_final_successes):
        field = "accepted_candidate_final_successes"
        reason = "count must match accepted candidates"
        raise ContractValidationError(field, reason)
    if not value.real_execution:
        exit_code = ExitCode.OK if value.dry_run else ExitCode.INCOMPLETE_OR_FAILURE
        return FinalSuccessEvaluation(
            status=RunStatus.INCOMPLETE_PRE_QWEN_PREVIEW,
            final_success=False,
            exit_code=exit_code,
        )
    if not value.requested_detector_lanes:
        field = "requested_detector_lanes"
        raise ContractValidationError(field, "must not be empty for real execution")
    if not _requested_lanes_real_executed(
        value.requested_detector_lanes, value.lane_receipts
    ):
        return FinalSuccessEvaluation(
            status=RunStatus.INCOMPLETE,
            final_success=False,
            exit_code=ExitCode.INCOMPLETE_OR_FAILURE,
        )
    if value.accepted_candidate_count == 0:
        return FinalSuccessEvaluation(
            status=RunStatus.NO_VALID_ROUGH_TARGETS,
            final_success=False,
            exit_code=ExitCode.INCOMPLETE_OR_FAILURE,
        )
    if not all(value.accepted_candidate_final_successes):
        return FinalSuccessEvaluation(
            status=RunStatus.INCOMPLETE,
            final_success=False,
            exit_code=ExitCode.INCOMPLETE_OR_FAILURE,
        )
    return FinalSuccessEvaluation(
        status=RunStatus.SUCCESS,
        final_success=True,
        exit_code=ExitCode.OK,
    )
