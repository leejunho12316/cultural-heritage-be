"""Post-RAG relation authority."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from modules.anomaly_grouping.geometry import (
    mask_area_ratio,
    mask_containment,
    mask_iou,
    mask_pixel_count,
    overlaps,
)
from modules.anomaly_grouping.ids import relation_group_id
from modules.anomaly_grouping.models import (
    AnomalyCandidate,
    RelationClass,
    RelationGroup,
    RelationMergeRequest,
    RelationMergeResult,
)
from modules.anomaly_grouping.relation_results import candidate_results
from modules.anomaly_grouping.shared_contracts import relation_outcomes

if TYPE_CHECKING:
    from modules.shared import RelationAuthorityInput

_DUPLICATE_IOU_THRESHOLD: Final = 0.75
_REFINEMENT_CONTAINMENT_THRESHOLD: Final = 0.80
_REFINEMENT_AREA_RATIO_THRESHOLD: Final = 0.35


# pipeline.py의 run_anomaly_grouping이 호출하는 유일한 병합 진입점(예전
# pre-RAG 그룹핑은 제거됨). 후보 쌍을 분류해 관계 그룹을 만들고, 병합 클래스로
# 판정된 그룹은 마스크 union으로 합쳐 최종 유지 상태를 산출한다.
def merge_post_rag_relations(request: RelationMergeRequest) -> RelationMergeResult:
    """Classify candidate pairs and merge mask-matching groups by pixel union."""
    candidates = tuple(
        sorted(request.candidates, key=lambda candidate: candidate.candidate_id)
    )
    relation_groups = tuple(
        group
        for left, right in _pairs(candidates)
        if (group := _relation_group(left, right)) is not None
    )
    return RelationMergeResult(
        relation_groups,
        candidate_results(candidates, relation_groups, request.mask_output_dir),
    )


# merge_post_rag_relations에서 같은 이미지 안의 후보 쌍마다 호출된다. 부모/자식
# 순서를 정하고 _classify로 관계를 분류해, 관계가 없으면 None을 반환한다.
def _relation_group(
    left: AnomalyCandidate,
    right: AnomalyCandidate,
) -> RelationGroup | None:
    parent, child = _parent_child(left, right)
    relation_class = _classify(parent, child)
    if relation_class is None:
        return None
    sources = tuple(sorted((parent.candidate_id, child.candidate_id)))
    return RelationGroup(
        relation_group_id(
            relation_class, parent.candidate_id, child.candidate_id, sources
        ),
        relation_class,
        parent.candidate_id,
        child.candidate_id,
        sources,
        _reasons(parent, child, relation_class),
        relation_outcomes(relation_class, (parent, child)),
    )


# 관계 판정의 핵심 규칙 - 기준은 마스크, bbox는 값싼 1차 필터일 뿐이다.
# bbox가 겹치지 않으면(=인접하지 않으면) 아예 무관계다. bbox가 겹치는데 개념이
# 다르면 CO_LOCATED_DISTINCT_ANOMALY(별개, 병합 안 함)로 남는다. bbox가 겹치고
# 개념도 같으면 반드시 병합되고, 마스크 IoU/포함율로 어떤 종류의 병합인지만
# 세분화한다(DUPLICATE > REFINEMENT > 그 외 ADJACENT).
def _classify(
    parent: AnomalyCandidate,
    child: AnomalyCandidate,
) -> RelationClass | None:
    if not overlaps(parent.bbox, child.bbox):
        return None
    if not (_concept_match(parent, child) and _descriptor_match(parent, child)):
        return RelationClass.CO_LOCATED_DISTINCT_ANOMALY
    if mask_iou(parent.mask, child.mask) >= _DUPLICATE_IOU_THRESHOLD:
        return RelationClass.SAME_ANOMALY_DUPLICATE
    if (
        mask_containment(child.mask, parent.mask) >= _REFINEMENT_CONTAINMENT_THRESHOLD
        and mask_area_ratio(child.mask, parent.mask) <= _REFINEMENT_AREA_RATIO_THRESHOLD
    ):
        return RelationClass.SAME_ANOMALY_REFINEMENT
    return RelationClass.SAME_ANOMALY_ADJACENT


# _relation_group에서 호출된다. 두 후보 중 마스크 픽셀 수가 더 많은 쪽을
# parent로 정해 reasons 문자열과 containment/area_ratio 계산 방향의 기준을
# 고정한다. bbox 면적이 아니라 마스크 픽셀 수를 쓰는 이유: _classify의
# containment/area_ratio 판정 자체가 마스크 기준이라, bbox 면적이 더 큰
# 쪽과 마스크 픽셀이 더 많은 쪽이 다를 때(성긴/가느다란 탐지 vs 조밀한
# 탐지) bbox로 고르면 REFINEMENT 방향 판정이 반대로 뒤집혀 실제로는 성립하는
# 관계를 놓칠 수 있다. 병합이 확정된 뒤에는 이 parent/child 구분에 최종
# 의미가 없다 - relation_results.py가 병합 그룹의 대표를 별도로(최소
# candidate_id) 정하고 마스크는 항상 union이다.
def _parent_child(
    left: AnomalyCandidate,
    right: AnomalyCandidate,
) -> tuple[AnomalyCandidate, AnomalyCandidate]:
    first, second = sorted(
        (left, right),
        key=lambda candidate: (
            -mask_pixel_count(candidate.mask),
            candidate.candidate_id,
        ),
    )
    return first, second


# merge_post_rag_relations에서 비교할 후보 쌍(조합)을 만든다.
def _pairs(
    candidates: tuple[AnomalyCandidate, ...],
) -> tuple[tuple[AnomalyCandidate, AnomalyCandidate], ...]:
    # Bbox geometry(겹침 사전 필터)는 하나의 이미지 픽셀 좌표 공간 안에서만
    # 의미가 있다 - 서로 다른 이미지의 후보끼리는 절대 비교하지 마라, 안 그러면
    # 서로 무관한 이미지들이 우연한 기하학적 일치로 "병합"될 수 있다.
    return tuple(
        (left, right)
        for index, left in enumerate(candidates)
        for right in candidates[index + 1 :]
        if left.image_id == right.image_id
    )


# _classify에서 사용. C-004 relation_authority_input이 있으면 그 구조화된
# concept_family_compatible 값을 신뢰하고, 없으면 concept_family 문자열 일치로
# 대체한다.
def _concept_match(left: AnomalyCandidate, right: AnomalyCandidate) -> bool:
    return _structured_compatibility(
        left.evidence.relation_authority_input,
        right.evidence.relation_authority_input,
        "concept_family_compatible",
        left.evidence.concept_family == right.evidence.concept_family,
    )


# _concept_match와 대칭되는 함수. descriptor_tokens 교집합 존재 여부를 기본
# 대체값으로 쓰고, C-004 구조화 입력이 있으면 그것을 우선한다.
def _descriptor_match(left: AnomalyCandidate, right: AnomalyCandidate) -> bool:
    left_tokens = frozenset(left.evidence.descriptor_tokens)
    right_tokens = frozenset(right.evidence.descriptor_tokens)
    fallback = bool(
        left_tokens and right_tokens and left_tokens.intersection(right_tokens)
    )
    return _structured_compatibility(
        left.evidence.relation_authority_input,
        right.evidence.relation_authority_input,
        "descriptor_compatible",
        fallback,
    )


# _concept_match/_descriptor_match가 공유하는 공통 로직: 두 후보 중 하나라도
# C-004 relation_authority_input을 가지면 구조화된 필드값을 쓰고, 둘 다 없으면
# 호출자가 넘긴 fallback(문자열 비교 결과)을 쓴다.
def _structured_compatibility(
    left: RelationAuthorityInput | None,
    right: RelationAuthorityInput | None,
    field_name: str,
    fallback: bool,
) -> bool:
    inputs = tuple(value for value in (left, right) if value is not None)
    if not inputs:
        return fallback
    return all(getattr(value, field_name) for value in inputs)


# _relation_group에서 RelationGroup.reasons에 담을 사람이 읽을 수 있는 근거
# 문자열(관계 클래스, 마스크 IoU/포함율 수치)을 만든다.
def _reasons(
    parent: AnomalyCandidate,
    child: AnomalyCandidate,
    relation_class: RelationClass,
) -> tuple[str, ...]:
    if relation_class is RelationClass.CO_LOCATED_DISTINCT_ANOMALY:
        return (relation_class.value, "bbox_overlap", "concept_mismatch")
    return (
        relation_class.value,
        f"mask_iou={mask_iou(parent.mask, child.mask):.3f}",
        f"mask_containment={mask_containment(child.mask, parent.mask):.3f}",
    )
