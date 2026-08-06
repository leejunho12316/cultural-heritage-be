"""Top-level anomaly grouping orchestration."""

from __future__ import annotations

from modules.anomaly_grouping.models import (
    AnomalyGroupingRequest,
    AnomalyGroupingResult,
    FollowupParentTarget,
    PreRagGroupingResult,
    RelationMergeRequest,
)
from modules.anomaly_grouping.pre_rag import group_pre_rag_candidates
from modules.anomaly_grouping.relations import merge_post_rag_relations


def run_anomaly_grouping(request: AnomalyGroupingRequest) -> AnomalyGroupingResult:
    """Run pre-RAG grouping and post-RAG relation authority."""
    pre_rag = group_pre_rag_candidates(
        request.pre_rag_candidates,
        request.seed_lane_priority,
    )
    relation_merge = merge_post_rag_relations(
        RelationMergeRequest(
            request.post_rag_candidates,
            request.phase,
            request.previous_suppressions,
            request.already_reopened_candidate_ids,
        )
    )
    return AnomalyGroupingResult(
        request.phase,
        pre_rag,
        relation_merge,
        _followup_targets(pre_rag),
    )


def _followup_targets(
    pre_rag: PreRagGroupingResult,
) -> tuple[FollowupParentTarget, ...]:
    group_targets = [
        FollowupParentTarget(
            "same_anomaly_group_id",
            group.group_id,
            group.parent_candidate_id,
        )
        for group in pre_rag.same_anomaly_groups
    ]
    candidate_targets = [
        FollowupParentTarget("candidate_id", str(candidate_id), candidate_id)
        for candidate_id in pre_rag.standalone_candidate_ids
    ]
    return (*group_targets, *candidate_targets)
