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
    FollowupParentTarget,
    MaskReference,
    RelationClass,
    RelationGroup,
    RelationMergeRequest,
    RelationMergeResult,
)
from modules.anomaly_grouping.pipeline import run_anomaly_grouping

__all__ = (
    "ANOMALY_GROUPING_REQUEST_SCHEMA",
    "ANOMALY_GROUPING_RESULT_SCHEMA",
    "AnomalyCandidate",
    "AnomalyGroupingRequest",
    "AnomalyGroupingResult",
    "BoundingBox",
    "CandidateEvidence",
    "CandidateRelationResult",
    "FollowupParentTarget",
    "MaskReference",
    "RelationClass",
    "RelationGroup",
    "RelationMergeRequest",
    "RelationMergeResult",
    "run_anomaly_grouping",
)
