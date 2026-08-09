"""Tile-boundary merge - a narrowed revival of the removed pre_rag.py.

The original pre_rag.py grouped any candidates that overlapped within the
same (source_object_id, source_view_id, seed_prompt) bucket, with no concept
information available yet (it ran pre-RAG). That let it merge two genuinely
different anomalies that just happened to be close together. It was removed
this session in favor of a single merge pass in relations.py, gated on
concept/descriptor match (not just geometry).

That fix left one real gap: rough_masking can split a single physical
anomaly across the overlap region of two adjacent ranked tiles (T4 tiling,
overlap ratio 0.33). Each tile-side fragment gets its own independent RAG
query, so relations.py's concept-match gate only merges them back together
if both fragments happen to receive *consistent* evidence - if one gets
"crack" and the other gets nothing (or something else), they stay as two
separate findings describing the same real damage.

This module closes that gap narrowly: it merges a candidate pair only when
both come from *different* tiles (source_tile_view_id set and unequal) of
the *same* object and their bboxes overlap - never same-tile pairs (that is
relations.py's job, with real concept evidence), never candidates without a
tile origin (a whole-object-crop candidate overlaps nearly everything in its
object and would cause runaway merging). It runs before relations.py, on the
same AnomalyCandidate shape (masks already restored to original-image
coordinates by mask_refining, so bboxes from different tiles are directly
comparable) - so by the time relations.py's concept-gated pass runs, a
tile-split anomaly is already a single candidate with a consistent identity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from modules.anomaly_grouping.geometry import (
    mask_bbox,
    mask_union_array,
    overlaps,
    write_mask_png,
)
from modules.anomaly_grouping.ids import merged_mask_filename
from modules.anomaly_grouping.models import AnomalyCandidate, MaskReference

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from modules.shared import CandidateId


def merge_tile_split_candidates(
    candidates: tuple[AnomalyCandidate, ...], mask_output_dir: Path
) -> tuple[AnomalyCandidate, ...]:
    """Merge candidate pairs that are tile-boundary duplicates of one object."""
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    merged: list[AnomalyCandidate] = []
    for member_ids in _tile_overlap_components(candidates):
        if len(member_ids) == 1:
            merged.append(by_id[member_ids[0]])
            continue
        merged.append(_union_candidate(member_ids, by_id, mask_output_dir))
    return tuple(sorted(merged, key=lambda candidate: candidate.candidate_id))


# 같은 오브젝트, 서로 다른 타일, bbox 겹침 - 이 세 조건을 모두 만족하는
# 쌍만 엣지로 연결해 union-find 연결요소를 만든다. source_tile_view_id가
# 없는 후보(오브젝트 크롭 전체에서 나온 후보)는 항상 자기 자신만의
# 크기 1 컴포넌트가 된다 - 오브젝트 크롭은 사실상 모든 타일과 겹치므로,
# 병합 대상에 넣으면 무관한 특이점까지 줄줄이 엮인다.
def _tile_overlap_components(
    candidates: tuple[AnomalyCandidate, ...],
) -> tuple[tuple[CandidateId, ...], ...]:
    parent: dict[CandidateId, CandidateId] = {
        candidate.candidate_id: candidate.candidate_id for candidate in candidates
    }

    def find(candidate_id: CandidateId) -> CandidateId:
        while parent[candidate_id] != candidate_id:
            parent[candidate_id] = parent[parent[candidate_id]]
            candidate_id = parent[candidate_id]
        return candidate_id

    by_object: dict[str, list[AnomalyCandidate]] = {}
    for candidate in candidates:
        if candidate.source_tile_view_id is None:
            continue
        by_object.setdefault(candidate.source_object_id, []).append(candidate)
    for members in by_object.values():
        _connect_overlapping_tile_pairs(members, find, parent)

    members_by_root: dict[CandidateId, list[CandidateId]] = {}
    for candidate in candidates:
        members_by_root.setdefault(find(candidate.candidate_id), []).append(
            candidate.candidate_id
        )
    return tuple(
        sorted(
            (tuple(sorted(members)) for members in members_by_root.values()),
            key=lambda members: members,
        )
    )


# _tile_overlap_components의 순환 복잡도를 낮추기 위해 분리한 내부 이중
# 루프 - 한 오브젝트의 후보들 안에서 서로 다른 타일 + bbox 겹침 쌍만
# union-find로 연결한다.
def _connect_overlapping_tile_pairs(
    members: list[AnomalyCandidate],
    find: Callable[[CandidateId], CandidateId],
    parent: dict[CandidateId, CandidateId],
) -> None:
    for i, left in enumerate(members):
        for right in members[i + 1 :]:
            if left.source_tile_view_id == right.source_tile_view_id:
                continue
            if not overlaps(left.bbox, right.bbox):
                continue
            left_root, right_root = find(left.candidate_id), find(right.candidate_id)
            if left_root != right_root:
                parent[right_root] = left_root


# 병합 그룹 하나를 마스크 union으로 합쳐 대표 후보 하나를 만든다. 대표
# candidate_id는 relation_results.py와 동일하게 결정론적인 최소값일 뿐,
# "더 나은 후보를 골랐다"는 의미는 없다 - concept_family/descriptor 등
# 나머지 필드도 그 대표 후보의 값을 그대로 쓴다(둘이 다르더라도 병합 후엔
# 하나로 통일된 값이 필요하고, 어느 쪽이 "맞는지" pre-relations 시점에는
# 판단할 근거가 없다).
def _union_candidate(
    member_ids: tuple[CandidateId, ...],
    by_id: dict[CandidateId, AnomalyCandidate],
    mask_output_dir: Path,
) -> AnomalyCandidate:
    root_id = min(member_ids)
    representative = by_id[root_id]
    masks = tuple(by_id[member_id].mask for member_id in member_ids)
    union_array = mask_union_array(masks)
    output_path = mask_output_dir / merged_mask_filename(root_id, member_ids)
    digest = write_mask_png(union_array, output_path)
    union_mask = MaskReference(str(output_path), digest)
    return AnomalyCandidate(
        root_id,
        representative.image_id,
        representative.source_object_id,
        representative.source_view_id,
        representative.seed_lane,
        representative.seed_prompt,
        mask_bbox(union_array),
        union_mask,
        representative.evidence,
        representative.duplicate_suppression_key,
        qwen_final_success=representative.qwen_final_success,
        qwen_report_display_text=representative.qwen_report_display_text,
        qwen_confidence=representative.qwen_confidence,
        source_tile_view_id=representative.source_tile_view_id,
    )
