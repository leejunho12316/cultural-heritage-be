"""Candidate RAG sidecar contracts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from modules.rag.qwen.qwen_bridge_artifacts import read_qwen_bridge_results
from modules.shared import ContractValidationError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from modules.prompt_generating import VisualCue
    from modules.rag.evidence.concept_cards import RagVisualConceptCard
    from modules.shared import (
        CandidateId,
        QwenBridgeResult,
    )

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | Sequence["JsonValue"] | dict[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]

RAG_CANDIDATE_EVIDENCE_SIDECAR: Final = "rag_candidate_evidence.jsonl"
RAG_VISUAL_CONCEPT_CARDS_SIDECAR: Final = "rag_visual_concept_cards.jsonl"
RAG_CANDIDATE_SIDECAR_MANIFEST: Final = "rag_candidate_sidecars_manifest.json"
RAG_EVIDENCE_READY: Final = "rag_evidence_ready"


@dataclass(frozen=True, slots=True)
class CandidateRagSidecarInputs:
    """Artifact paths and explicit optional inputs for candidate sidecars."""

    rough_records_root: Path
    queries_path: Path
    prompt_rag_results_path: Path
    qwen_results: Mapping[CandidateId, QwenBridgeResult] = field(
        default_factory=lambda: MappingProxyType({})
    )
    qwen_bridge_results_path: Path | None = None
    visual_cues: Mapping[CandidateId, VisualCue] = field(
        default_factory=lambda: MappingProxyType({})
    )

    # Qwen 결과를 mapping과 디렉터리 경로 두 가지로 동시에 주는 모호한 입력을
    # 막는다. 정확히 하나만 지정해야 resolved_qwen_results가 결정적으로 동작한다.
    def __post_init__(self) -> None:
        """Reject ambiguous Qwen bridge sources before sidecar construction."""
        if self.qwen_bridge_results_path is not None and self.qwen_results:
            field = "qwen_results"
            reason = "provide a mapping or qwen_bridge_results_path, not both"
            raise ContractValidationError(
                field,
                reason,
            )

    # candidate_sidecars.build_candidate_rag_sidecars가 실제 Qwen 결과를 얻을 때
    # 호출하는 지연 로딩 지점. 두 입력 방식(명시적 mapping vs 디렉터리 경로) 중
    # 어느 쪽이 쓰였는지 여기서 한 번만 분기한다.
    def resolved_qwen_results(self) -> Mapping[CandidateId, QwenBridgeResult]:
        """Return explicit Qwen results or parse the supplied artifact directory."""
        if self.qwen_bridge_results_path is None:
            return self.qwen_results
        if not self.qwen_bridge_results_path.is_dir():
            field = "qwen_bridge_results_path"
            reason = "must reference a readable Qwen result directory"
            raise ContractValidationError(
                field, reason
            )
        return read_qwen_bridge_results(self.qwen_bridge_results_path)


@dataclass(frozen=True, slots=True)
class CandidateRagEvidenceRow:
    """One rough candidate joined to pre-Qwen RAG retrieval evidence."""

    lane: str
    prompt_text: str
    query_id: str | None
    rough_record_path: str
    rough_record_index: int
    rag_parent_candidate_id: str
    matched_citation_ids: tuple[str, ...]
    matched_chunk_ids: tuple[str, ...]
    top_citation_id: str | None
    top_chunk_id: str | None
    top_result_rank: int | None
    top_retrieval_score: float | None
    evidence_state: str
    evidence_reason: str | None


@dataclass(frozen=True, slots=True)
class CandidateRagSidecarResult:
    """In-memory candidate mapping rows and renderable card rows."""

    evidence_rows: tuple[CandidateRagEvidenceRow, ...]
    cards: tuple[RagVisualConceptCard, ...]
