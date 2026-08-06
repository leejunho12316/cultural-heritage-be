"""Shared-row construction for RAG target accounting."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NoReturn

from modules.rag.operations.targets import RagTarget
from modules.shared import (
    ContractValidationError,
    QwenBridgeStatus,
    RagAccountingRow,
    RagAccountingStatus,
)


@dataclass(frozen=True, slots=True)
class RagQueryEvidence:
    """Query-driving evidence and Qwen state used to construct a shared row."""

    qwen_status: QwenBridgeStatus
    rag_query_terms: tuple[str, ...]
    rag_query_descriptors: tuple[str, ...]
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        """Reject query evidence that conflicts with its Qwen state."""
        match self.qwen_status:
            case QwenBridgeStatus.SUCCESS:
                if not self.rag_query_terms and not self.rag_query_descriptors:
                    _raise_contract(
                        "rag_query",
                        "qwen-success evidence requires query terms or descriptors",
                    )
                if self.failure_reason is not None:
                    _raise_contract(
                        "failure_reason",
                        "qwen-success evidence cannot include a Qwen failure reason",
                    )
            case QwenBridgeStatus.FAILED:
                if self.rag_query_terms or self.rag_query_descriptors:
                    _raise_contract(
                        "rag_query",
                        "qwen-failed evidence has no Qwen query terms",
                    )
                if self.failure_reason is None or not self.failure_reason.strip():
                    _raise_contract(
                        "failure_reason",
                        "qwen-failed evidence requires a reason",
                    )


@dataclass(frozen=True, slots=True)
class TargetAccounting:
    """A complete target paired with its shared accounting row."""

    target: RagTarget
    row: RagAccountingRow

    def __post_init__(self) -> None:
        """Reject rows that drift from target identity or parent validity."""
        if self.row.candidate_id != self.target.candidate_id:
            _raise_contract(
                "accounting.candidate_id",
                "must match target candidate_id",
            )
        if self.row.followup_mode is not self.target.followup_mode:
            _raise_contract(
                "accounting.followup_mode",
                "must match target followup_mode",
            )
        invalid_parent_status = (
            self.row.status is RagAccountingStatus.FAILED_INVALID_PARENT_TARGET
        )
        if self.target.selected_parent.valid is invalid_parent_status:
            _raise_contract(
                "accounting.status",
                "must explicitly match selected-parent validity",
            )


@dataclass(frozen=True, slots=True)
class _RowOutcome:
    status: RagAccountingStatus
    failure_reason: str | None = None


def account_target(target: RagTarget, row: RagAccountingRow) -> TargetAccounting:
    """Pair target metadata with one shared accounting row."""
    return TargetAccounting(target, row)


def require_terminal_accounting(
    targets: Sequence[RagTarget],
    records: Sequence[TargetAccounting],
) -> tuple[TargetAccounting, ...]:
    """Require exactly one terminal shared row for every supplied target."""
    if Counter(targets) != Counter(record.target for record in records):
        _raise_contract(
            "accounting.targets",
            "each target requires exactly one accounting record",
        )
    if any(not record.row.terminal for record in records):
        _raise_contract(
            "accounting.status",
            "every final accounting record must be terminal",
        )
    return tuple(records)


def completed_row(target: RagTarget, evidence: RagQueryEvidence) -> RagAccountingRow:
    """Create a terminal completed row."""
    return _row(target, evidence, _RowOutcome(RagAccountingStatus.COMPLETED))


def failed_no_citation_row(
    target: RagTarget,
    evidence: RagQueryEvidence,
    reason: str,
) -> RagAccountingRow:
    """Create a terminal no-citation row."""
    return _row(
        target,
        evidence,
        _RowOutcome(RagAccountingStatus.FAILED_NO_CITATION, reason),
    )


def failed_no_visual_cue_row(
    target: RagTarget,
    evidence: RagQueryEvidence,
    reason: str,
) -> RagAccountingRow:
    """Create a terminal no-visual-cue row."""
    return _row(
        target,
        evidence,
        _RowOutcome(RagAccountingStatus.FAILED_NO_VISUAL_CUE, reason),
    )


def invalid_parent_row(
    target: RagTarget,
    evidence: RagQueryEvidence,
    reason: str,
) -> RagAccountingRow:
    """Create a terminal invalid-parent row."""
    return _row(
        target,
        evidence,
        _RowOutcome(RagAccountingStatus.FAILED_INVALID_PARENT_TARGET, reason),
    )


def budget_blocked_row(
    target: RagTarget,
    evidence: RagQueryEvidence,
    reason: str,
) -> RagAccountingRow:
    """Create a terminal budget-blocked row."""
    return _row(
        target,
        evidence,
        _RowOutcome(RagAccountingStatus.BLOCKED_BY_BUDGET_APPROVAL, reason),
    )


def same_anomaly_suppressed_row(
    target: RagTarget,
    evidence: RagQueryEvidence,
    reason: str,
) -> RagAccountingRow:
    """Create a terminal same-anomaly suppression row."""
    return _row(
        target,
        evidence,
        _RowOutcome(RagAccountingStatus.SKIPPED_SUPPRESSED_WITH_PARENT, reason),
    )


def failed_qwen_unavailable_row(
    target: RagTarget,
    evidence: RagQueryEvidence,
) -> RagAccountingRow:
    """Create a terminal failed-Qwen row without query-driving terms."""
    return _row(
        target,
        evidence,
        _RowOutcome(RagAccountingStatus.FAILED_QWEN_UNAVAILABLE),
    )


def reopen_completed_row(
    target: RagTarget,
    evidence: RagQueryEvidence,
) -> RagAccountingRow:
    """Create a terminal reopened-completion row."""
    return _row(target, evidence, _RowOutcome(RagAccountingStatus.REOPEN_COMPLETED))


def reopen_skipped_row(
    target: RagTarget,
    evidence: RagQueryEvidence,
    reason: str,
) -> RagAccountingRow:
    """Create a terminal reopened-skip row."""
    return _row(
        target,
        evidence,
        _RowOutcome(RagAccountingStatus.REOPEN_SKIPPED, reason),
    )


def reopen_blocked_row(
    target: RagTarget,
    evidence: RagQueryEvidence,
    reason: str,
) -> RagAccountingRow:
    """Create a terminal reopened-budget-block row."""
    return _row(
        target,
        evidence,
        _RowOutcome(RagAccountingStatus.REOPEN_BLOCKED, reason),
    )


def reopen_forbidden_final_row(
    target: RagTarget,
    evidence: RagQueryEvidence,
    reason: str,
) -> RagAccountingRow:
    """Create a terminal final-pass reopen-forbidden row."""
    return _row(
        target,
        evidence,
        _RowOutcome(RagAccountingStatus.REOPEN_FORBIDDEN_FINAL, reason),
    )


def attempt_created_row(
    target: RagTarget,
    evidence: RagQueryEvidence,
) -> RagAccountingRow:
    """Create a non-terminal initial-attempt row."""
    return _row(target, evidence, _RowOutcome(RagAccountingStatus.ATTEMPT_CREATED))


def reopen_required_row(
    target: RagTarget,
    evidence: RagQueryEvidence,
) -> RagAccountingRow:
    """Create a non-terminal reopen-required row."""
    return _row(target, evidence, _RowOutcome(RagAccountingStatus.REOPEN_REQUIRED))


def reopen_created_row(
    target: RagTarget,
    evidence: RagQueryEvidence,
) -> RagAccountingRow:
    """Create a non-terminal reopened-attempt row."""
    return _row(target, evidence, _RowOutcome(RagAccountingStatus.REOPEN_CREATED))


def _row(
    target: RagTarget,
    evidence: RagQueryEvidence,
    outcome: _RowOutcome,
) -> RagAccountingRow:
    failure_reason = outcome.failure_reason or evidence.failure_reason
    return RagAccountingRow(
        candidate_id=target.candidate_id,
        followup_mode=target.followup_mode,
        status=outcome.status,
        qwen_status=evidence.qwen_status,
        rag_query_terms=evidence.rag_query_terms,
        rag_query_descriptors=evidence.rag_query_descriptors,
        failure_reason=failure_reason,
    )


def _raise_contract(field: str, reason: str) -> NoReturn:
    raise ContractValidationError(field, reason)
