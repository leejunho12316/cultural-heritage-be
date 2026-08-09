"""Candidate keep/absorb results and mask-union materialization."""

from __future__ import annotations

from typing import TYPE_CHECKING

from modules.anomaly_grouping.geometry import (
    load_mask_array,
    mask_bbox,
    mask_polygons,
    mask_union_array,
    write_mask_png,
)
from modules.anomaly_grouping.ids import merged_mask_filename
from modules.anomaly_grouping.models import (
    CandidateRelationResult,
    MaskReference,
    RelationClass,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from modules.anomaly_grouping.models import (
        AnomalyCandidate,
        BoundingBox,
        RelationGroup,
    )
    from modules.shared import CandidateId

# 이 세 관계 클래스만 실제로 병합(마스크 union)을 일으킨다.
# CO_LOCATED_DISTINCT_ANOMALY는 겹치거나 인접해도 개념이 달라 별개로 남는다.
_MERGE_CLASSES: frozenset[RelationClass] = frozenset(
    (
        RelationClass.SAME_ANOMALY_DUPLICATE,
        RelationClass.SAME_ANOMALY_REFINEMENT,
        RelationClass.SAME_ANOMALY_ADJACENT,
    )
)


# relations.py의 merge_post_rag_relations가 호출한다. 병합 클래스 관계들로
# 연결요소(병합 그룹)를 만들고, 그룹마다 마스크를 픽셀 union으로 합쳐
# mask_output_dir에 기록한 뒤, 후보별 최종 유지/흡수 상태를 반환한다. 그룹의
# 대표 id는 병합 전 어떤 후보가 "더 나았는지"와 무관하게 결정적으로 정해지는
# 최소 candidate_id일 뿐이다 - 실제 내용(마스크/bbox)은 항상 union이다.
def candidate_results(
    candidates: tuple[AnomalyCandidate, ...],
    relation_groups: tuple[RelationGroup, ...],
    mask_output_dir: Path,
) -> dict[CandidateId, CandidateRelationResult]:
    """Resolve per-candidate kept state, merging masks for merge-class groups."""
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    results: dict[CandidateId, CandidateRelationResult] = {}
    for member_ids, group_relation_id in _merge_components(candidates, relation_groups):
        root_id = min(member_ids)
        if len(member_ids) == 1:
            candidate = by_id[root_id]
            results[root_id] = CandidateRelationResult(
                root_id,
                kept=True,
                mask=candidate.mask,
                bbox=candidate.bbox,
                polygons=mask_polygons(load_mask_array(candidate.mask)),
            )
            continue
        union_mask, union_bbox, union_polygons = _materialize_union_mask(
            root_id, member_ids, by_id, mask_output_dir
        )
        results[root_id] = CandidateRelationResult(
            root_id,
            kept=True,
            mask=union_mask,
            bbox=union_bbox,
            polygons=union_polygons,
            relation_group_id=group_relation_id,
        )
        for member_id in member_ids:
            if member_id == root_id:
                continue
            results[member_id] = CandidateRelationResult(
                member_id,
                kept=False,
                inherited_parent_candidate_id=root_id,
                relation_group_id=group_relation_id,
            )
    return results


# candidate_results 내부 헬퍼. 병합 클래스 관계 엣지만으로 union-find 연결
#요소를 구성한다 - CO_LOCATED_DISTINCT_ANOMALY 엣지는 무시되므로 별개
# 특이점끼리는 절대 같은 컴포넌트에 들어가지 않는다. 관계가 하나도 없는
# 후보는 자기 자신만의 컴포넌트(크기 1)가 된다.
def _merge_components(
    candidates: tuple[AnomalyCandidate, ...],
    relation_groups: tuple[RelationGroup, ...],
) -> tuple[tuple[tuple[CandidateId, ...], str | None], ...]:
    parent: dict[CandidateId, CandidateId] = {
        candidate.candidate_id: candidate.candidate_id for candidate in candidates
    }

    def find(candidate_id: CandidateId) -> CandidateId:
        while parent[candidate_id] != candidate_id:
            parent[candidate_id] = parent[parent[candidate_id]]
            candidate_id = parent[candidate_id]
        return candidate_id

    merge_relations = tuple(
        relation
        for relation in relation_groups
        if relation.relation_class in _MERGE_CLASSES
    )
    for relation in merge_relations:
        left_root = find(relation.parent_candidate_id)
        right_root = find(relation.child_candidate_id)
        if left_root != right_root:
            parent[right_root] = left_root

    members_by_root: dict[CandidateId, list[CandidateId]] = {}
    for candidate in candidates:
        members_by_root.setdefault(find(candidate.candidate_id), []).append(
            candidate.candidate_id
        )

    relation_id_by_root: dict[CandidateId, str] = {}
    for relation in merge_relations:
        root = find(relation.parent_candidate_id)
        relation_id_by_root.setdefault(root, relation.relation_group_id)

    return tuple(
        sorted(
            (
                (tuple(sorted(members)), relation_id_by_root.get(root))
                for root, members in members_by_root.items()
            ),
            key=lambda item: item[0],
        )
    )


# candidate_results에서 크기 2 이상인 병합 그룹마다 호출된다. 구성원 마스크의
# 픽셀 union을 계산해 PNG로 기록하고, 그 union에서 파생된 bbox/폴리곤과 함께
# MaskReference를 반환한다.
def _materialize_union_mask(
    root_id: CandidateId,
    member_ids: tuple[CandidateId, ...],
    by_id: Mapping[CandidateId, AnomalyCandidate],
    mask_output_dir: Path,
) -> tuple[MaskReference, BoundingBox, tuple[tuple[tuple[float, float], ...], ...]]:
    masks = tuple(by_id[member_id].mask for member_id in member_ids)
    union_array = mask_union_array(masks)
    output_path = mask_output_dir / merged_mask_filename(root_id, member_ids)
    digest = write_mask_png(union_array, output_path)
    return (
        MaskReference(str(output_path), digest),
        mask_bbox(union_array),
        mask_polygons(union_array),
    )
