"""Top-level anomaly grouping orchestration."""

from __future__ import annotations

from modules.anomaly_grouping.models import (
    AnomalyGroupingRequest,
    AnomalyGroupingResult,
    FollowupParentTarget,
    RelationMergeRequest,
    RelationMergeResult,
)
from modules.anomaly_grouping.relations import merge_post_rag_relations


# 모듈 전체의 최상위 진입점. startup_runner.py와 runner.py(CLI) 양쪽에서
# 호출되며, post-RAG 관계 판정(겹침+개념 일치 기반 병합, 마스크 union)을 실행해
# AnomalyGroupingResult를 만든다. 예전에 있던 pre-RAG 그룹핑 단계는 개념
# 정보 없이 겹치기만 하면 서로 다른 특이점도 합쳐버릴 위험이 있어 제거했다 -
# 병합 판정은 이제 이 한 곳(relations.py)에서만 일어난다.
def run_anomaly_grouping(request: AnomalyGroupingRequest) -> AnomalyGroupingResult:
    """Run post-RAG relation authority (the only merge pass)."""
    relation_merge = merge_post_rag_relations(
        RelationMergeRequest(request.candidates, request.mask_output_dir)
    )
    return AnomalyGroupingResult(
        relation_merge,
        _followup_targets(relation_merge),
    )


# run_anomaly_grouping에서 관계 병합 결과를 사용자 후속 조치(follow-up)가
# 가리킬 수 있는 안정적 선택자 목록으로 변환한다. 병합돼 살아남은 대표
# candidate_id 자체가 이미 안정적인 선택자이므로(마스크가 union으로 갱신돼도
# ID는 그대로), 예전처럼 별도의 same_anomaly_group_id가 필요 없다.
def _followup_targets(
    relation_merge: RelationMergeResult,
) -> tuple[FollowupParentTarget, ...]:
    return tuple(
        FollowupParentTarget("candidate_id", str(candidate_id), candidate_id)
        for candidate_id, result in sorted(relation_merge.candidate_results.items())
        if result.kept
    )
