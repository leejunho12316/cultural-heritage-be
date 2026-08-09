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
    """Closed relation classes owned by anomaly grouping.

    The first three all trigger a mask-union merge (see relations.py); only
    CO_LOCATED_DISTINCT_ANOMALY keeps both candidates separate.
    """

    SAME_ANOMALY_DUPLICATE = "same_anomaly_duplicate"
    SAME_ANOMALY_REFINEMENT = "same_anomaly_refinement"
    SAME_ANOMALY_ADJACENT = "same_anomaly_adjacent"
    CO_LOCATED_DISTINCT_ANOMALY = "co_located_distinct_anomaly"


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Positive-area xyxy bounding box.

    Auxiliary/derived geometry only - the mask is the standard. bbox exists
    for cheap pairwise prefiltering and for consumers that have not moved to
    mask geometry yet.
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
    """Content-addressed reference to a segmentation mask PNG on disk.

    `path` is an absolute filesystem path (not root-relative) so every
    consumer can load it without also needing to know which stage's output
    root it came from - rough_masking, mask_refining, and anomaly_grouping's
    own materialized union masks all produce these the same way.
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
    """Per-candidate keep/absorb result after relation merge.

    A kept candidate always carries the mask downstream stages should use:
    its own original mask when it was never merged, or the pixel union of
    its whole merge group when it is a merged group's canonical id. `bbox`
    and `polygons` are both *derived from that same mask* (not the original
    detector bbox), so display/report code never has to choose which one is
    authoritative - `polygons` are the vectorized outlines the FE renders,
    one per disconnected mask fragment (real masks are often multi-component
    - e.g. scattered corrosion spots - so a single polygon would either drop
    minor fragments or falsely bridge the gaps between them), bbox stays for
    auxiliary/legacy display. An absorbed (kept=False) candidate has none of
    these anymore - its pixels live on in the group's union, referenced via
    inherited_parent_candidate_id.
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
