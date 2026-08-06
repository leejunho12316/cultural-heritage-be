"""Typed contracts for anomaly relation authority."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, NoReturn

from modules.shared import (
    CandidateId,
    ContractValidationError,
    HybridDescriptor,
    RagAccountingStatus,
    RelationAuthorityInput,
    RelationAuthorityOutcome,
)

ANOMALY_GROUPING_REQUEST_SCHEMA: Final = "anomaly_grouping_request_v1"
ANOMALY_GROUPING_RESULT_SCHEMA: Final = "anomaly_grouping_result_v1"


class MergePhase(StrEnum):
    """Relation merge pass with different reopen semantics."""

    INITIAL_RELATION_MERGE = "initial_relation_merge"
    FINAL_RELATION_MERGE = "final_relation_merge"


class RelationClass(StrEnum):
    """Closed relation classes owned by anomaly grouping."""

    SAME_ANOMALY_DUPLICATE = "same_anomaly_duplicate"
    SAME_ANOMALY_REFINEMENT = "same_anomaly_refinement"
    CO_LOCATED_DISTINCT_ANOMALY = "co_located_distinct_anomaly"
    CONTEXT_CONTAINS = "context_contains"


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Positive-area xyxy bounding box."""

    x_min: float
    y_min: float
    x_max: float
    y_max: float

    @property
    def area(self) -> float:
        """Return the box area."""
        return (self.x_max - self.x_min) * (self.y_max - self.y_min)

    def __post_init__(self) -> None:
        """Reject non-finite or non-positive boxes."""
        values = (self.x_min, self.y_min, self.x_max, self.y_max)
        if not all(math.isfinite(value) for value in values):
            _raise_contract("bbox_xyxy", "coordinates must be finite")
        if self.x_max <= self.x_min or self.y_max <= self.y_min:
            _raise_contract("bbox_xyxy", "must have positive area")


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    """Structured visual evidence allowed for relation authority."""

    concept_family: str = "unknown"
    descriptor_tokens: tuple[str, ...] = ()
    concept_card_ids: tuple[str, ...] = ()
    provenance_strength: str = "unknown"
    evidence_flags: tuple[str, ...] = ()
    rag_status: RagAccountingStatus = RagAccountingStatus.COMPLETED
    visual_cue_confidence: float | None = None
    hybrid_descriptor: HybridDescriptor | None = None
    relation_authority_input: RelationAuthorityInput | None = None


@dataclass(frozen=True, slots=True)
class AnomalyCandidate:
    """Candidate shape consumed by pre-RAG grouping and relation authority."""

    candidate_id: CandidateId
    source_object_id: str
    source_view_id: str
    seed_lane: str
    seed_prompt: str
    bbox: BoundingBox
    evidence: CandidateEvidence = field(default_factory=CandidateEvidence)
    explicit_pre_rag_parent_id: CandidateId | None = None
    duplicate_suppression_key: str | None = None

    def __post_init__(self) -> None:
        """Reject blank candidate identity fields."""
        for field_name, value in (
            ("candidate_id", self.candidate_id),
            ("source_object_id", self.source_object_id),
            ("source_view_id", self.source_view_id),
            ("seed_lane", self.seed_lane),
            ("seed_prompt", self.seed_prompt),
        ):
            if not value.strip():
                _raise_contract(field_name, "must not be blank")


@dataclass(frozen=True, slots=True)
class SameAnomalyGroup:
    """Pre-RAG same-anomaly group with one selected parent."""

    group_id: str
    parent_candidate_id: CandidateId
    member_candidate_ids: tuple[CandidateId, ...]
    suppressed_candidate_ids: tuple[CandidateId, ...]


@dataclass(frozen=True, slots=True)
class PreRagGroupingResult:
    """Pre-RAG grouping output for RAG target accounting."""

    same_anomaly_groups: tuple[SameAnomalyGroup, ...]
    standalone_candidate_ids: tuple[CandidateId, ...]


@dataclass(frozen=True, slots=True)
class PreviousSuppression:
    """Suppressed child metadata from an earlier same-anomaly decision."""

    candidate_id: CandidateId
    parent_candidate_id: CandidateId
    same_anomaly_group_id: str


@dataclass(frozen=True, slots=True)
class RelationGroup:
    """One structured relation authority decision for a candidate pair."""

    relation_group_id: str
    relation_class: RelationClass
    parent_candidate_id: CandidateId
    child_candidate_id: CandidateId
    source_candidate_ids: tuple[CandidateId, ...]
    reasons: tuple[str, ...]
    relation_authority_outcomes: tuple[RelationAuthorityOutcome, ...] = ()


@dataclass(frozen=True, slots=True)
class CandidateRelationResult:
    """Per-candidate keep/inherit result after relation merge."""

    candidate_id: CandidateId
    kept: bool
    inherited_parent_candidate_id: CandidateId | None = None
    relation_group_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReopenEvent:
    """Bounded reopen request for a suppressed child reclassified as distinct."""

    event_id: str
    candidate_id: CandidateId
    previous_parent_candidate_id: CandidateId
    previous_same_anomaly_group_id: str
    reason_code: str
    status: RagAccountingStatus = RagAccountingStatus.REOPEN_REQUIRED


@dataclass(frozen=True, slots=True)
class FinalReopenRejection:
    """Terminal final-pass record when reopening is no longer allowed."""

    candidate_id: CandidateId
    previous_parent_candidate_id: CandidateId
    previous_same_anomaly_group_id: str
    reason_code: str
    status: RagAccountingStatus


@dataclass(frozen=True, slots=True)
class FollowupParentTarget:
    """Stable selector exposed for user-requested follow-up targets."""

    selector_type: str
    selector_id: str
    candidate_id: CandidateId


@dataclass(frozen=True, slots=True)
class RelationMergeRequest:
    """Inputs for post-RAG relation authority."""

    candidates: tuple[AnomalyCandidate, ...]
    phase: MergePhase
    previous_suppressions: tuple[PreviousSuppression, ...] = ()
    already_reopened_candidate_ids: tuple[CandidateId, ...] = ()


@dataclass(frozen=True, slots=True)
class RelationMergeResult:
    """Outputs from post-RAG relation authority."""

    phase: MergePhase
    relation_groups: tuple[RelationGroup, ...]
    candidate_results: dict[CandidateId, CandidateRelationResult]
    reopen_events: tuple[ReopenEvent, ...]
    final_reopen_rejections: tuple[FinalReopenRejection, ...]


@dataclass(frozen=True, slots=True)
class AnomalyGroupingRequest:
    """Top-level request for the standalone anomaly grouping runner."""

    phase: MergePhase
    seed_lane_priority: tuple[str, ...]
    pre_rag_candidates: tuple[AnomalyCandidate, ...]
    post_rag_candidates: tuple[AnomalyCandidate, ...]
    previous_suppressions: tuple[PreviousSuppression, ...] = ()
    already_reopened_candidate_ids: tuple[CandidateId, ...] = ()


@dataclass(frozen=True, slots=True)
class AnomalyGroupingResult:
    """Top-level anomaly grouping result artifact."""

    phase: MergePhase
    pre_rag: PreRagGroupingResult
    relation_merge: RelationMergeResult
    followup_parent_targets: tuple[FollowupParentTarget, ...]


def _raise_contract(field_name: str, reason: str) -> NoReturn:
    raise ContractValidationError(field_name, reason)
