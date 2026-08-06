"""Adapters from RAG concept evidence to prompt generation."""

from modules.prompt_generating import (
    ConceptCard,
    PromptVariant,
    render_lane_specific_variants,
)
from modules.rag.evidence.concept_cards import RagVisualConceptCard


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


def render_rag_prompt_variants(
    card: RagVisualConceptCard,
) -> tuple[PromptVariant, ...]:
    """Render executable variants through prompt-generation ownership."""
    return render_lane_specific_variants(
        adapt_to_prompt_concept_card(card),
        card.visual_cue,
    )
