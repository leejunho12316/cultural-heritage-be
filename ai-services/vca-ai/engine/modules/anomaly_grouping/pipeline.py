"""Top-level anomaly grouping orchestration."""

from __future__ import annotations

from modules.anomaly_grouping.models import (
    AnomalyGroupingRequest,
    AnomalyGroupingResult,
    FollowupParentTarget,
    RelationMergeResult,
)
from modules.anomaly_grouping.relation_results import candidate_results


# 모듈 전체의 최상위 진입점. startup_runner.py와 runner.py(CLI) 양쪽에서
# 호출된다. 물리적으로 같은 특이점을 병합하는 작업(마스크 겹침 + concept_family
# + morphology 일치)은 이제 이 단계보다 앞선, rag 직후 단계
# (modules/anomaly_grouping/pre_refinement_merge.py)에서 딱 한 번 끝난다.
# mask_refining이 만드는 각 accepted_candidate는 이미 병합이 끝난 최종 단위이므로
# 여기서는 더 합칠 게 없다 - relation_groups를 빈 튜플로 candidate_results에
# 넘기면(병합 그룹이 하나도 없을 때의 기존 동작) 모든 후보가 그대로 kept=True로
# 남는다. 이 함수의 역할은 이제 그룹핑이 아니라, mask_refining 출력을
# report_generating이 기대하는 모양으로 조립하는 것뿐이다.
def run_anomaly_grouping(request: AnomalyGroupingRequest) -> AnomalyGroupingResult:
    """Assemble final per-candidate report state - merging already happened upstream."""
    relation_merge = RelationMergeResult(
        (), candidate_results(request.candidates, (), request.mask_output_dir)
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
