"""Adapters from RAG concept evidence to prompt generation."""

from modules.prompt_generating import (
    ConceptCard,
    PromptVariant,
    render_lane_specific_variants,
)
from modules.rag.evidence.concept_cards import RagVisualConceptCard


# RAG 소유 카드를 prompt_generating 모듈이 이해하는 ConceptCard로 변환한다.
# render_rag_prompt_variants가 렌더링 직전에 호출한다.
def adapt_to_prompt_concept_card(card: RagVisualConceptCard) -> ConceptCard:
    """Convert a RAG concept card into the prompt-generation model."""
    return ConceptCard(
        concept_card_id=card.concept_card_id,
        rag_parent_candidate_id=card.rag_parent_candidate_id,
        concept_family=card.concept_family,
        descriptor_terms=card.descriptor_terms,
        material_terms=card.material_terms,
        context_terms=card.context_terms,
        source_citation_ids=card.source_citation_ids,
        raw_retrieved_sentence=card.raw_retrieved_sentence,
    )


# RAG 카드 한 장으로부터 실행 가능한 프롬프트 변형들을 만든다. 실제 렌더링
# 로직은 prompt_generating 쪽 소유이며, 여기서는 어댑터 역할만 한다.
# candidate_sidecars._dedupe_cards/evidence.py._visual_tokens 등에서 호출된다.
def render_rag_prompt_variants(
    card: RagVisualConceptCard,
) -> tuple[PromptVariant, ...]:
    """Render executable variants through prompt-generation ownership."""
    return render_lane_specific_variants(
        adapt_to_prompt_concept_card(card),
        card.visual_cue,
    )
