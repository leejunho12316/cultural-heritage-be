"""Typed contracts for anomaly relation authority."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Final, NoReturn

from modules.shared import (
    CandidateId,
    ContractValidationError,
    HybridDescriptor,
    RagAccountingStatus,
    RelationAuthorityInput,
    RelationAuthorityOutcome,
)

if TYPE_CHECKING:
    from pathlib import Path

ANOMALY_GROUPING_REQUEST_SCHEMA: Final = "anomaly_grouping_request_v1"
ANOMALY_GROUPING_RESULT_SCHEMA: Final = "anomaly_grouping_result_v1"


class RelationClass(StrEnum):
    """anomaly grouping이 소유하는, 고정된 관계 클래스 집합.

    앞의 세 개는 모두 마스크 합집합 병합을 트리거한다(relations.py 참고);
    CO_LOCATED_DISTINCT_ANOMALY만 두 후보를 계속 별개로 유지한다.
    """

    SAME_ANOMALY_DUPLICATE = "same_anomaly_duplicate"
    SAME_ANOMALY_REFINEMENT = "same_anomaly_refinement"
    SAME_ANOMALY_ADJACENT = "same_anomaly_adjacent"
    CO_LOCATED_DISTINCT_ANOMALY = "co_located_distinct_anomaly"


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """면적이 양수인 xyxy 바운딩 박스.

    보조/파생 geometry일 뿐이다 - 기준(standard)은 마스크다. bbox는 저비용
    pairwise 사전 필터링을 위해, 그리고 아직 마스크 geometry로 옮겨가지 않은
    소비자들을 위해 존재한다.
    """

    x_min: float
    y_min: float
    x_max: float
    y_max: float

    @property
    def area(self) -> float:
        """Return the box area."""
        return (self.x_max - self.x_min) * (self.y_max - self.y_min)

    # 데이터클래스 생성 시 자동 호출되는 검증 훅. 좌표가 유한하지 않거나 면적이
    # 0/음수인 잘못된 박스가 파이프라인에 들어오는 것을 여기서 막는다.
    def __post_init__(self) -> None:
        """Reject non-finite or non-positive boxes."""
        values = (self.x_min, self.y_min, self.x_max, self.y_max)
        if not all(math.isfinite(value) for value in values):
            _raise_contract("bbox_xyxy", "coordinates must be finite")
        if self.x_max <= self.x_min or self.y_max <= self.y_min:
            _raise_contract("bbox_xyxy", "must have positive area")


@dataclass(frozen=True, slots=True)
class MaskReference:
    """디스크에 있는 세그멘테이션 마스크 PNG를 콘텐츠 주소로 가리키는 참조.

    `path`는 (루트 상대 경로가 아니라) 절대 파일시스템 경로이므로, 모든
    소비자는 이게 어느 스테이지의 output root에서 나왔는지 몰라도 그대로
    로드할 수 있다 - rough_masking, mask_refining, 그리고
    anomaly_grouping 자체가 materialize한 합집합 마스크 모두 이걸 같은
    방식으로 만들어낸다.
    """

    path: str
    sha256: str

    # 데이터클래스 생성 시 자동 호출되는 검증 훅. 빈 경로/해시가 들어오는 것을
    # 여기서 막는다.
    def __post_init__(self) -> None:
        """Reject blank path or hash fields."""
        if not self.path.strip():
            _raise_contract("mask.path", "must not be blank")
        if not self.sha256.strip():
            _raise_contract("mask.sha256", "must not be blank")


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    """Structured visual evidence allowed for relation authority."""

    concept_family: str = "unknown"
    descriptor_tokens: tuple[str, ...] = ()
    concept_card_ids: tuple[str, ...] = ()
    provenance_strength: str = "unknown"
    evidence_flags: tuple[str, ...] = ()
    rag_status: RagAccountingStatus = RagAccountingStatus.COMPLETED
    visual_cue_confidence: float | None = None
    hybrid_descriptor: HybridDescriptor | None = None
    relation_authority_input: RelationAuthorityInput | None = None


@dataclass(frozen=True, slots=True)
class AnomalyCandidate:
    """Candidate shape consumed by relation authority."""

    candidate_id: CandidateId
    image_id: str
    source_object_id: str
    source_view_id: str
    seed_lane: str
    seed_prompt: str
    bbox: BoundingBox
    mask: MaskReference
    evidence: CandidateEvidence = field(default_factory=CandidateEvidence)
    duplicate_suppression_key: str | None = None
    qwen_final_success: bool = False
    qwen_report_display_text: str = "없음"
    qwen_confidence: float | None = None
    # rough_masking이 오브젝트 크롭이 아니라 타일 뷰에서 이 후보를 찾았다면
    # 채워진다. tile_merge가 서로 다른 타일에서 나온 후보 쌍만 병합 대상으로
    # 좁히는 데 쓴다 - 같은 타일 안에서 겹치는 쌍은 타일 분할 때문이 아니라
    # relations.py가 이미 다루는 일반적인 같은-특이점 판정 대상이다.
    source_tile_view_id: str | None = None

    # 데이터클래스 생성 시 자동 호출되는 검증 훅. candidate_id/image_id 등 식별용
    # 문자열 필드가 빈 값으로 들어오는 것을 여기서 막는다.
    def __post_init__(self) -> None:
        """Reject blank candidate identity fields."""
        for field_name, value in (
            ("candidate_id", self.candidate_id),
            ("image_id", self.image_id),
            ("source_object_id", self.source_object_id),
            ("source_view_id", self.source_view_id),
            ("seed_lane", self.seed_lane),
            ("seed_prompt", self.seed_prompt),
        ):
            if not value.strip():
                _raise_contract(field_name, "must not be blank")


@dataclass(frozen=True, slots=True)
class RelationGroup:
    """One structured relation authority decision for a candidate pair."""

    relation_group_id: str
    relation_class: RelationClass
    parent_candidate_id: CandidateId
    child_candidate_id: CandidateId
    source_candidate_ids: tuple[CandidateId, ...]
    reasons: tuple[str, ...]
    relation_authority_outcomes: tuple[RelationAuthorityOutcome, ...] = ()


@dataclass(frozen=True, slots=True)
class CandidateRelationResult:
    """관계 병합 이후의, 후보별 keep/absorb 결과.

    kept(유지된) 후보는 항상 다운스트림 스테이지가 써야 할 마스크를
    갖고 있다: 한 번도 병합되지 않았다면 자기 자신의 원본 마스크를, 병합된
    그룹의 canonical id라면 그 병합 그룹 전체의 픽셀 합집합을 갖는다.
    `bbox`와 `polygons`는 둘 다 (원래 detector bbox가 아니라) *바로 그
    마스크에서 파생*되므로, 표시/리포트 코드는 어느 쪽이 기준인지 고를
    필요가 없다 - `polygons`는 FE가 렌더링하는 벡터화된 윤곽선이며 끊어진
    마스크 조각마다 하나씩 있다(실제 마스크는 다중 컴포넌트인 경우가
    흔하다 - 예: 흩어진 부식 반점들 - 그래서 폴리곤 하나로만 그리면 작은
    조각들이 빠지거나 그 사이 간격이 잘못 이어져 버린다), bbox는 보조/레거시
    표시용으로 남아 있다. absorbed(kept=False)된 후보는 더 이상 이런 걸 하나도
    갖지 않는다 - 그 픽셀은 그룹의 합집합 안에 계속 남아 있으며,
    inherited_parent_candidate_id로 참조된다.
    """

    candidate_id: CandidateId
    kept: bool
    mask: MaskReference | None = None
    bbox: BoundingBox | None = None
    polygons: tuple[tuple[tuple[float, float], ...], ...] | None = None
    inherited_parent_candidate_id: CandidateId | None = None


@dataclass(frozen=True, slots=True)
class FollowupParentTarget:
    """Stable selector exposed for user-requested follow-up targets."""

    selector_type: str
    selector_id: str
    candidate_id: CandidateId


@dataclass(frozen=True, slots=True)
class RelationMergeRequest:
    """Inputs for post-RAG relation authority."""

    candidates: tuple[AnomalyCandidate, ...]
    mask_output_dir: Path


@dataclass(frozen=True, slots=True)
class RelationMergeResult:
    """Outputs from post-RAG relation authority."""

    relation_groups: tuple[RelationGroup, ...]
    candidate_results: dict[CandidateId, CandidateRelationResult]


@dataclass(frozen=True, slots=True)
class AnomalyGroupingRequest:
    """Top-level request for the standalone anomaly grouping runner."""

    candidates: tuple[AnomalyCandidate, ...]
    mask_output_dir: Path


@dataclass(frozen=True, slots=True)
class AnomalyGroupingResult:
    """Top-level anomaly grouping result artifact."""

    relation_merge: RelationMergeResult
    followup_parent_targets: tuple[FollowupParentTarget, ...]


def _raise_contract(field_name: str, reason: str) -> NoReturn:
    raise ContractValidationError(field_name, reason)
