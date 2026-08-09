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
from modules.anomaly_grouping.tile_merge import merge_tile_split_candidates


# 모듈 전체의 최상위 진입점. startup_runner.py와 runner.py(CLI) 양쪽에서
# 호출된다. 예전에 있던 pre-RAG 그룹핑 단계(pre_rag.py)는 개념 정보 없이
# 겹치기만 하면 서로 다른 특이점도 합쳐버릴 위험이 있어 이번 세션에
# 제거했었다 - 그 자리를 대체한 게 두 단계다: (1) tile_merge.py가 타일
# 분할로 인한 중복만 좁게(같은 오브젝트+다른 타일+bbox 겹침) 병합하고,
# (2) relations.py가 나머지 전부를 겹침+개념/서술어 일치 기준으로
# 병합한다. tile_merge가 먼저 도는 이유: 타일 경계에서 잘린 후보는 각자
# 독립적으로 RAG 질의를 받아서 서로 다른(또는 한쪽만 있는) 근거를 받을 수
# 있는데, concept 일치를 요구하는 relations.py 혼자서는 이 경우를 못
# 합친다 - tile_merge는 타일 출처만으로 병합 여부를 정하므로 이 문제가
# 아예 없다.
def run_anomaly_grouping(request: AnomalyGroupingRequest) -> AnomalyGroupingResult:
    """Merge tile-split duplicates, then run post-RAG relation authority."""
    tile_merged_candidates = merge_tile_split_candidates(
        request.candidates, request.mask_output_dir
    )
    relation_merge = merge_post_rag_relations(
        RelationMergeRequest(tile_merged_candidates, request.mask_output_dir)
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
