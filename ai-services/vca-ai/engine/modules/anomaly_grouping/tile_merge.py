"""타일 경계 병합 - 제거된 pre_rag.py를 좁은 범위로 되살린 것.

원래 pre_rag.py는 concept 정보가 아직 없는 상태(RAG 이전에 실행됐으므로)로,
같은 (source_object_id, source_view_id, seed_prompt) 버킷 안에서 겹치는
후보라면 뭐든 그룹핑했다. 그러다 보니 우연히 가까이 있을 뿐인, 실제로는
서로 다른 두 이상 소견을 병합해버릴 수 있었다. 이번 세션에서 이걸 제거하고
relations.py의 단일 병합 패스로 대체했는데, 이건 (geometry뿐 아니라)
concept/descriptor 일치 여부로 게이팅된다.

그 수정은 진짜 빈틈 하나를 남겼다: rough_masking은 인접한 두 랭킹 타일이
겹치는 영역(T4 타일링, overlap ratio 0.33)에서 하나의 물리적 이상 소견을
쪼갤 수 있다. 각 타일 쪽 조각은 각자 독립적인 RAG 쿼리를 받으므로,
relations.py의 concept-match 게이트는 두 조각이 우연히 *일관된* 근거를
받았을 때만 다시 하나로 합친다 - 한쪽은 "crack"을 받고 다른 쪽은 아무것도
(또는 다른 걸) 받지 못하면, 같은 실제 손상을 설명하는 두 개의 별도
finding으로 남는다.

이 모듈은 그 빈틈을 좁은 범위로 메운다: 후보 쌍이 *같은* object의
*서로 다른* 타일(source_tile_view_id가 설정돼 있고 서로 다름)에서 왔고
bbox가 겹칠 때만 병합한다 - 같은 타일 쌍은 절대 병합하지 않고(그건 실제
concept 근거를 가진 relations.py의 일이다), 타일 출처가 없는 후보도 절대
병합하지 않는다(object crop 전체를 대상으로 한 후보는 자기 object의 거의
모든 것과 겹치므로 통제 불능의 연쇄 병합을 일으킬 것이다). relations.py보다
먼저, 같은 AnomalyCandidate 형태(마스크는 이미 mask_refining이 원본 이미지
좌표로 복원해뒀으므로 서로 다른 타일의 bbox를 바로 비교할 수 있다)에 대해
실행된다 - 그래서 relations.py의 concept 게이팅 패스가 실행될 때쯤에는,
타일로 쪼개졌던 이상 소견이 이미 일관된 identity를 가진 후보 하나로
합쳐져 있다.
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
