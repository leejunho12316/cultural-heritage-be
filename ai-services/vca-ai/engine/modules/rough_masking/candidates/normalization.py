"""Runner invocation and receipt construction for rough candidate normalization."""

from dataclasses import dataclass

from modules.rough_masking.artifacts.assets import AssetReference
from modules.rough_masking.artifacts.records import decode_records
from modules.rough_masking.candidates import (
    CandidateStatus,
    RawDetectorCandidate,
    normalize_candidate,
)
from modules.rough_masking.contracts import AdapterRequest, DetectorRunner
from modules.shared import (
    ContractValidationError,
    LaneExecutionStatus,
)

__all__ = (
    "AdapterReceipt",
    "AssetReference",
    "CandidateStatus",
    "NoFakeClaimAudit",
    "RawDetectorCandidate",
    "execute_adapter",
)


@dataclass(frozen=True, slots=True)
class NoFakeClaimAudit:
    """Lane-level evidence that a production lane did not invent output."""

    active_lane_only: bool
    fabricated_candidate_count: int
    runner_invoked: bool


@dataclass(frozen=True, slots=True)
class AdapterReceipt:
    """One lane's runner evidence, diagnostics, and accepted candidates."""

    lane: str
    status: LaneExecutionStatus
    candidates: tuple[RawDetectorCandidate, ...]
    diagnostics: tuple[str, ...]
    no_fake_claim_audit: NoFakeClaimAudit


def execute_adapter(request: AdapterRequest, runner: DetectorRunner) -> AdapterReceipt:
    """Invoke the supplied runner and normalize only valid accepted records."""
    outcome = runner(request)
    if not outcome.runner_invoked:
        field = "runner_invoked"
        reason = "real execution requires evidence"
        raise ContractValidationError(field, reason)
    audit = NoFakeClaimAudit(
        active_lane_only=True,
        fabricated_candidate_count=0,
        runner_invoked=True,
    )
    if (
        request.lane_output_dir.name != request.lane.value
        or not request.lane_output_dir.is_dir()
    ):
        return AdapterReceipt(
            request.lane.value,
            LaneExecutionStatus.FAILED,
            (),
            ("lane_output_dir_invalid",),
            audit,
        )
    if (
        not request.records_json.is_file()
        or not request.records_json.resolve().is_relative_to(
            request.lane_output_dir.resolve()
        )
    ):
        return AdapterReceipt(
            request.lane.value,
            LaneExecutionStatus.FAILED,
            (),
            ("records_json_invalid",),
            audit,
        )
    try:
        decoded = decode_records(request.records_json.read_text())
    except OSError:
        return AdapterReceipt(
            request.lane.value,
            LaneExecutionStatus.FAILED,
            (),
            ("records_json_invalid",),
            audit,
        )
    if decoded is None:
        return AdapterReceipt(
            request.lane.value,
            LaneExecutionStatus.FAILED,
            (),
            ("records_json_invalid",),
            audit,
        )
    if not decoded:
        return AdapterReceipt(
            request.lane.value,
            LaneExecutionStatus.REAL_EXECUTED,
            (),
            ("records_empty",),
            audit,
        )
    candidates: list[RawDetectorCandidate] = []
    diagnostics: list[str] = []
    for record in decoded:
        candidate, diagnostic = normalize_candidate(request, record)
        if candidate is not None:
            candidates.append(candidate)
        elif diagnostic is not None:
            diagnostics.append(diagnostic)
    return AdapterReceipt(
        request.lane.value,
        LaneExecutionStatus.REAL_EXECUTED,
        tuple(candidates),
        tuple(diagnostics),
        audit,
    )
