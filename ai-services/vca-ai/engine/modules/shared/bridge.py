"""Shared C-004 RAG relation bridge contracts."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, NoReturn

from modules.shared.errors import ContractValidationError
from modules.shared.models import CandidateId, FollowupMode
from modules.shared.qwen_bridge import QwenBridgeStatus


class RagAccountingStatus(StrEnum):
    """C-004 RAG accounting statuses with explicit terminal semantics."""

    ATTEMPT_CREATED = "attempt_created"
    COMPLETED = "completed"
    SKIPPED_SUPPRESSED_WITH_PARENT = "skipped_suppressed_with_parent"
    BLOCKED_BY_BUDGET_APPROVAL = "blocked_by_budget_approval"
    FAILED_NO_CITATION = "failed_no_citation"
    FAILED_NO_VISUAL_CUE = "failed_no_visual_cue"
    FAILED_INVALID_PARENT_TARGET = "failed_invalid_parent_target"
    FAILED_QWEN_UNAVAILABLE = "failed_qwen_unavailable"


class RelationAuthorityOutcomeState(StrEnum):
    """C-004 relation classes emitted by relation authority."""

    SAME_ANOMALY_DUPLICATE = "same_anomaly_duplicate"
    SAME_ANOMALY_REFINEMENT = "same_anomaly_refinement"
    SAME_ANOMALY_ADJACENT = "same_anomaly_adjacent"
    CO_LOCATED_DISTINCT_ANOMALY = "co_located_distinct_anomaly"


_NON_TERMINAL_RAG_STATUSES: Final = frozenset(
    {
        RagAccountingStatus.ATTEMPT_CREATED,
    }
)
_QWEN_FAILED_RAG_STATUSES: Final = frozenset(
    {
        RagAccountingStatus.SKIPPED_SUPPRESSED_WITH_PARENT,
        RagAccountingStatus.BLOCKED_BY_BUDGET_APPROVAL,
        RagAccountingStatus.FAILED_NO_CITATION,
        RagAccountingStatus.FAILED_NO_VISUAL_CUE,
        RagAccountingStatus.FAILED_INVALID_PARENT_TARGET,
        RagAccountingStatus.FAILED_QWEN_UNAVAILABLE,
    }
)
_QWEN_FAILURE_ONLY_RAG_STATUSES: Final = (RagAccountingStatus.FAILED_QWEN_UNAVAILABLE,)


def terminal_rag_statuses() -> tuple[RagAccountingStatus, ...]:
    """Return RAG statuses that may close an accounting row."""
    return tuple(
        status
        for status in RagAccountingStatus
        if status not in _NON_TERMINAL_RAG_STATUSES
    )


def is_terminal_rag_status(status: RagAccountingStatus) -> bool:
    """Return whether a RAG status can close accounting for a target."""
    return status not in _NON_TERMINAL_RAG_STATUSES


@dataclass(frozen=True, slots=True)
class RagAccountingRow:
    """One explicit RAG accounting row for a detector/Qwen target."""

    candidate_id: CandidateId
    followup_mode: FollowupMode
    status: RagAccountingStatus
    qwen_status: QwenBridgeStatus
    rag_query_terms: tuple[str, ...]
    rag_query_descriptors: tuple[str, ...]
    failure_reason: str | None = None

    @property
    def terminal(self) -> bool:
        """Return the status-derived terminality for this accounting row."""
        return is_terminal_rag_status(self.status)

    def __post_init__(self) -> None:
        """Reject Qwen/RAG accounting state combinations that cannot finalize."""
        if self.qwen_status is QwenBridgeStatus.FAILED and (
            self.rag_query_terms or self.rag_query_descriptors
        ):
            _raise_contract("rag_query", "qwen-failed rows have no qwen query terms")
        if self.qwen_status is QwenBridgeStatus.FAILED:
            _validate_qwen_failed_row(self)
        if (
            self.qwen_status is QwenBridgeStatus.SUCCESS
            and self.status in _QWEN_FAILURE_ONLY_RAG_STATUSES
        ):
            _raise_contract("status", "qwen-success row cannot use qwen-failed status")


@dataclass(frozen=True, slots=True)
class ExportCitationBridge:
    """One export citation identifier paired with its adapter-owned status."""

    citation_id: str
    status: str


@dataclass(frozen=True, slots=True)
class HybridDescriptor:
    """Structured visual evidence bridge between RAG and relation authority."""

    candidate_id: CandidateId
    concept_family: str
    visual_descriptor_tokens: tuple[str, ...]
    qwen_selected_terms: tuple[str, ...]
    qwen_extracted_descriptors: tuple[str, ...]
    concept_card_ids: tuple[str, ...]
    export_citations: tuple[ExportCitationBridge, ...]
    source_cue_ids: tuple[str, ...]
    provenance_strength: str
    evidence_flags: tuple[str, ...]

    def __post_init__(self) -> None:
        """Reject descriptors without a visual evidence anchor."""
        if not self.concept_family.strip():
            _raise_contract("concept_family", "must not be blank")
        if not self.visual_descriptor_tokens:
            _raise_contract("visual_descriptor_tokens", "must not be empty")


@dataclass(frozen=True, slots=True)
class RelationAuthorityInput:
    """Structured-only relation input derived from C-004 bridge evidence."""

    candidate_id: CandidateId
    hybrid_descriptor: HybridDescriptor
    geometry_metric_ids: tuple[str, ...]
    same_object_ids: tuple[str, ...]
    source_view_ids: tuple[str, ...]
    duplicate_suppression_key: str
    concept_family_compatible: bool
    descriptor_compatible: bool
    citation_provenance_strength: str
    rag_status: RagAccountingStatus
    evidence_flags: tuple[str, ...]

    def __post_init__(self) -> None:
        """Reject non-terminal RAG state at relation-authority handoff."""
        if self.candidate_id != self.hybrid_descriptor.candidate_id:
            _raise_contract("candidate_id", "must match hybrid descriptor")
        if not is_terminal_rag_status(self.rag_status):
            _raise_contract("rag_status", "must be terminal")
        if not self.duplicate_suppression_key.strip():
            _raise_contract("duplicate_suppression_key", "must not be blank")


@dataclass(frozen=True, slots=True)
class RelationAuthorityOutcome:
    """Structured terminal relation outcome preserving C-004 bridge evidence."""

    candidate_id: CandidateId
    hybrid_descriptor: HybridDescriptor
    relation_authority_input: RelationAuthorityInput
    relation_authority_outcome: RelationAuthorityOutcomeState
    rag_status: RagAccountingStatus
    evidence_flags: tuple[str, ...]

    def __post_init__(self) -> None:
        """Reject relation outcomes that drift from their structured input."""
        if self.candidate_id != self.hybrid_descriptor.candidate_id:
            _raise_contract("candidate_id", "must match hybrid descriptor")
        if self.candidate_id != self.relation_authority_input.candidate_id:
            _raise_contract("candidate_id", "must match relation input")
        if self.hybrid_descriptor != self.relation_authority_input.hybrid_descriptor:
            _raise_contract("hybrid_descriptor", "must match relation input")
        if self.rag_status is not self.relation_authority_input.rag_status:
            _raise_contract("rag_status", "must match relation input")
        if type(self.relation_authority_outcome) is not RelationAuthorityOutcomeState:
            _raise_contract(
                "relation_authority_outcome",
                "must be known relation state",
            )


def _validate_qwen_failed_row(row: RagAccountingRow) -> None:
    if row.status not in _QWEN_FAILED_RAG_STATUSES:
        _raise_contract("status", "qwen-failed row requires qwen failure status")
    if row.failure_reason is None or not row.failure_reason.strip():
        _raise_contract("failure_reason", "qwen-failed row requires reason")


def _raise_contract(field: str, reason: str) -> NoReturn:
    raise ContractValidationError(field, reason)
