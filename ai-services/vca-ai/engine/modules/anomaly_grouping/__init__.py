"""Relation authority for same-anomaly and co-located anomaly grouping."""

from modules.anomaly_grouping.models import (
    ANOMALY_GROUPING_REQUEST_SCHEMA,
    ANOMALY_GROUPING_RESULT_SCHEMA,
    AnomalyCandidate,
    AnomalyGroupingRequest,
    AnomalyGroupingResult,
    BoundingBox,
    CandidateEvidence,
    CandidateRelationResult,
    FinalReopenRejection,
    FollowupParentTarget,
    MergePhase,
    PreRagGroupingResult,
    PreviousSuppression,
    RelationClass,
    RelationGroup,
    RelationMergeRequest,
    RelationMergeResult,
    ReopenEvent,
    SameAnomalyGroup,
)
from modules.anomaly_grouping.pipeline import run_anomaly_grouping
from modules.anomaly_grouping.pre_rag import group_pre_rag_candidates
from modules.anomaly_grouping.relations import merge_post_rag_relations

__all__ = (
    "ANOMALY_GROUPING_REQUEST_SCHEMA",
    "ANOMALY_GROUPING_RESULT_SCHEMA",
    "AnomalyCandidate",
    "AnomalyGroupingRequest",
    "AnomalyGroupingResult",
    "BoundingBox",
    "CandidateEvidence",
    "CandidateRelationResult",
    "FinalReopenRejection",
    "FollowupParentTarget",
    "MergePhase",
    "PreRagGroupingResult",
    "PreviousSuppression",
    "RelationClass",
    "RelationGroup",
    "RelationMergeRequest",
    "RelationMergeResult",
    "ReopenEvent",
    "SameAnomalyGroup",
    "group_pre_rag_candidates",
    "merge_post_rag_relations",
    "run_anomaly_grouping",
)
