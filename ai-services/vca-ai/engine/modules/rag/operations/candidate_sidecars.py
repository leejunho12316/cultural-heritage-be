"""Build candidate-level RAG mapping/accounting sidecars."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from modules.rag.evidence.concept_cards import RagVisualConceptCard, rank_concept_cards
from modules.rag.evidence.prompt_adapter import render_rag_prompt_variants
from modules.rag.operations.candidate_card_terms import (
    concept_family,
    context_terms,
    descriptor_terms,
    is_actionable_visual_cue,
    is_usable_retrieval_result,
    material_terms,
    normalize_visual_cue,
    provenance_strength_for_result,
    qwen_query_signature,
    retrieval_visual_cue,
)
from modules.rag.operations.candidate_sidecar_artifacts import (
    PromptQueryRecord,
    PromptRagResultRecord,
    RoughRagCandidate,
    joined_query,
    joined_results,
    query_index,
    read_queries,
    read_results,
    read_rough_records,
    result_index,
)
from modules.rag.operations.candidate_sidecar_models import (
    RAG_CANDIDATE_EVIDENCE_SIDECAR,
    RAG_CANDIDATE_SIDECAR_MANIFEST,
    RAG_EVIDENCE_READY,
    RAG_VISUAL_CONCEPT_CARDS_SIDECAR,
    CandidateRagEvidenceRow,
    CandidateRagSidecarInputs,
    CandidateRagSidecarResult,
)
from modules.rag.operations.candidate_sidecar_writers import (
    write_candidate_rag_sidecars,
)
from modules.rag.qwen import qwen_bridge_visual_cues

if TYPE_CHECKING:
    from collections.abc import Mapping

    from modules.prompt_generating import VisualConceptFamily, VisualCue
    from modules.shared import CandidateId, QwenBridgeResult

__all__ = (
    "RAG_CANDIDATE_EVIDENCE_SIDECAR",
    "RAG_CANDIDATE_SIDECAR_MANIFEST",
    "RAG_EVIDENCE_READY",
    "RAG_VISUAL_CONCEPT_CARDS_SIDECAR",
    "CandidateRagEvidenceRow",
    "CandidateRagSidecarInputs",
    "CandidateRagSidecarResult",
    "build_candidate_rag_sidecars",
    "write_candidate_rag_sidecars",
)


@dataclass(frozen=True, slots=True)
class _CandidateBuildOutcome:
    query: PromptQueryRecord | None
    results: tuple[PromptRagResultRecord, ...]
    cue: VisualCue | None
    evidence_state: str
    evidence_reason: str | None


@dataclass(frozen=True, slots=True)
class _CandidateSidecarSources:
    queries: Mapping[tuple[str, str, tuple[str, ...]], tuple[PromptQueryRecord, ...]]
    results_by_query: Mapping[str, tuple[PromptRagResultRecord, ...]]
    visual_cues: Mapping[CandidateId, VisualCue]
    # 시각단서로 변환되기 전의 원본 Qwen 결과. qwen_query_signature가 후보별
    # 질의 조인 시그니처를 계산할 때 필요하다(visual_cues는 이미 VisualCue로
    # 가공돼 있어 재사용할 수 없다).
    qwen_results: Mapping[CandidateId, QwenBridgeResult]


# RAG 스테이지의 핵심 조립 함수. startup_runner.run_rag_stage가 호출하며,
# rough 후보 각각을 쿼리/검색결과/Qwen 시각단서와 조인해 evidence row와
# (조건을 만족하면) concept 카드를 만든 뒤 카드 목록을 랭킹/중복제거한다.
def build_candidate_rag_sidecars(
    inputs: CandidateRagSidecarInputs,
) -> CandidateRagSidecarResult:
    """Build candidate-level mapping rows and gated visual concept cards."""
    rough_records = read_rough_records(inputs.rough_records_root)
    qwen_results = inputs.resolved_qwen_results()
    qwen_cues = qwen_bridge_visual_cues(qwen_results)
    sources = _CandidateSidecarSources(
        queries=query_index(read_queries(inputs.queries_path)),
        results_by_query=result_index(read_results(inputs.prompt_rag_results_path)),
        visual_cues={**qwen_cues, **inputs.visual_cues},
        qwen_results=qwen_results,
    )
    rows: list[CandidateRagEvidenceRow] = []
    cards: list[RagVisualConceptCard] = []
    for rough in rough_records:
        row, card = _build_candidate(rough, sources)
        rows.append(row)
        if card is not None:
            cards.append(card)
    return CandidateRagSidecarResult(
        evidence_rows=tuple(rows),
        cards=_dedupe_cards(rank_concept_cards(tuple(cards), limit=len(cards))),
    )


# rough 후보 하나를 쿼리/검색결과와 조인하고 evidence 상태를 판정한 뒤,
# 준비 상태(RAG_EVIDENCE_READY)일 때만 카드 생성을 시도한다.
# build_candidate_rag_sidecars의 반복문에서 후보마다 호출된다.
def _build_candidate(
    rough: RoughRagCandidate,
    sources: _CandidateSidecarSources,
) -> tuple[CandidateRagEvidenceRow, RagVisualConceptCard | None]:
    qwen_signature = qwen_query_signature(rough.candidate_id, sources.qwen_results)
    query, query_reason = joined_query(rough, qwen_signature, sources.queries)
    results, result_reason = joined_results(query, sources.results_by_query)
    cue = sources.visual_cues.get(rough.candidate_id)
    family = concept_family(rough.prompt_text)
    state, reason = _evidence_state(query_reason, result_reason, results)
    outcome = _CandidateBuildOutcome(query, results, cue, state, reason)
    row = _evidence_row(rough, outcome)
    if state != RAG_EVIDENCE_READY:
        return row, None
    card = _first_prompt_ready_card(rough, results, cue, family)
    return row, card


# 순위대로 검색 결과를 훑어 테이블 잡음이 아니고(actionable) 실행 가능한
# 시각 단서를 만들어낼 수 있는 첫 결과로 카드를 만든다. "최고 점수"가 아니라
# "가장 먼저 조건을 만족하는" 결과를 쓴다는 점에 유의. _build_candidate가 호출한다.
def _first_prompt_ready_card(
    rough: RoughRagCandidate,
    results: tuple[PromptRagResultRecord, ...],
    explicit_cue: VisualCue | None,
    family: VisualConceptFamily,
) -> RagVisualConceptCard | None:
    for result in results:
        if not is_usable_retrieval_result(result):
            continue
        cue = (
            normalize_visual_cue(explicit_cue)
            if explicit_cue is not None
            else retrieval_visual_cue(result, family)
        )
        if cue is None or not is_actionable_visual_cue(cue):
            continue
        card = _card(rough, result, cue, family)
        _ = render_rag_prompt_variants(card)
        return card
    return None


# 쿼리 조인 실패 → 결과 조인 실패 → 결과 없음 → 준비 완료 순으로 우선순위를
# 매겨 하나의 evidence_state/reason으로 요약한다. _build_candidate가 호출한다.
def _evidence_state(
    query_reason: str | None,
    result_reason: str | None,
    results: tuple[PromptRagResultRecord, ...],
) -> tuple[str, str | None]:
    if query_reason is not None:
        return query_reason, query_reason
    if result_reason is not None:
        return result_reason, result_reason
    if not results:
        return "no_citation", "no_citation"
    return RAG_EVIDENCE_READY, None


# _build_candidate의 결과(outcome)를 sidecar에 기록할 CandidateRagEvidenceRow로
# 펼친다. 결과가 없으면 top_* 필드들은 모두 None으로 남는다.
def _evidence_row(
    rough: RoughRagCandidate,
    outcome: _CandidateBuildOutcome,
) -> CandidateRagEvidenceRow:
    results = outcome.results
    top_result = results[0] if results else None
    return CandidateRagEvidenceRow(
        lane=rough.lane,
        prompt_text=rough.prompt_text,
        query_id=outcome.query.query_id if outcome.query is not None else None,
        rough_record_path=rough.rough_record_path,
        rough_record_index=rough.rough_record_index,
        rag_parent_candidate_id=rough.candidate_id,
        matched_citation_ids=tuple(result.citation_id for result in results),
        matched_chunk_ids=tuple(result.chunk_id for result in results),
        top_citation_id=top_result.citation_id if top_result is not None else None,
        top_chunk_id=top_result.chunk_id if top_result is not None else None,
        top_result_rank=top_result.rank if top_result is not None else None,
        top_retrieval_score=top_result.score if top_result is not None else None,
        evidence_state=outcome.evidence_state,
        evidence_reason=outcome.evidence_reason,
    )


# 검색 결과 하나와 시각 단서를 RagVisualConceptCard로 조립한다.
# _first_prompt_ready_card가 조건을 만족하는 결과를 찾았을 때 호출한다.
def _card(
    rough: RoughRagCandidate,
    result: PromptRagResultRecord,
    cue: VisualCue,
    family: VisualConceptFamily,
) -> RagVisualConceptCard:
    return RagVisualConceptCard(
        concept_card_id=(
            f"rag-card:{rough.candidate_id}:{result.citation_id}:{result.chunk_id}"
        ),
        rag_parent_candidate_id=rough.candidate_id,
        image_id=rough.image_id,
        concept_family=family,
        descriptor_terms=descriptor_terms(result, cue, family),
        material_terms=material_terms(result),
        context_terms=context_terms(result),
        source_citation_ids=(result.citation_id,),
        raw_retrieved_sentence=result.snippet_text,
        visual_cue=cue,
        retrieval_score=result.score,
        provenance_strength=provenance_strength_for_result(result),
    )


# 렌더링된 프롬프트 문자열이 완전히 같은 카드를 걸러낸다.
# build_candidate_rag_sidecars가 랭킹 이후 마지막 단계로 호출한다.
def _dedupe_cards(
    cards: tuple[RagVisualConceptCard, ...]
) -> tuple[RagVisualConceptCard, ...]:
    selected: list[RagVisualConceptCard] = []
    # 이미 본 프롬프트 집합은 image_id별로 범위가 나뉜다: 다른 이미지에서
    # 동일한 생성 프롬프트가 나와도 그건 중복이 아니라 별개의 실제 탐지다.
    seen_prompts_by_image: dict[str, set[str]] = {}
    for card in sorted(cards, key=_card_strength_key):
        prompts = tuple(
            variant.generated_prompt for variant in render_rag_prompt_variants(card)
        )
        seen_prompts = seen_prompts_by_image.setdefault(card.image_id, set())
        if any(prompt in seen_prompts for prompt in prompts):
            continue
        selected.append(card)
        seen_prompts.update(prompts)
    return tuple(selected)


# 신뢰도 → 검색 점수 → id 순 정렬 키. _dedupe_cards가 어떤 중복 카드를 남길지
# 정할 때 사용한다.
def _card_strength_key(card: RagVisualConceptCard) -> tuple[float, float, str]:
    return (-card.visual_cue.confidence, -card.retrieval_score, card.concept_card_id)
