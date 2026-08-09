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
from modules.anomaly_grouping.relations import merge_post_rag_relations
from modules.anomaly_grouping.tile_merge import merge_tile_split_candidates

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
    "merge_post_rag_relations",
    "merge_tile_split_candidates",
    "run_anomaly_grouping",
)
