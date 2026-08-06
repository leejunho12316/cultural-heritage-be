"""RAG-owned concept evidence records and deterministic card ranking."""

from dataclasses import dataclass
from enum import StrEnum, unique

from modules.prompt_generating import VisualConceptFamily, VisualCue
from modules.rag.evidence.citations import (
    CitationAdapterResult,
    ExportCitation,
    adapt_corpus_citation,
)
from modules.rag.retrieval.retrieval import RetrievalResult
from modules.rag.retrieval.terms import QueryTerms


@unique
class RetrievalStatus(StrEnum):
    """Terminal retrieval evidence states for concept-card construction."""

    RETRIEVED = "retrieved"
    NO_CITATION = "no_citation"


@unique
class CitationStatus(StrEnum):
    """Citation exportability states derived through the RAG citation adapter."""

    EXPORTABLE = "exportable"
    NON_EXPORTABLE = "non_exportable"
    NO_CITATION = "no_citation"


@dataclass(frozen=True, slots=True)
class RagConceptEvidence:
    """Retrieval, query, cue, and citation evidence for one RAG concept."""

    retrieval_result: RetrievalResult
    query_terms: QueryTerms
    visual_cue: VisualCue
    citation_results: tuple[CitationAdapterResult, ...]
    retrieval_status: RetrievalStatus
    citation_status: CitationStatus
    provenance_strength: str

    @classmethod
    def from_retrieval(
        cls,
        retrieval_result: RetrievalResult,
        query_terms: QueryTerms,
        visual_cue: VisualCue,
    ) -> "RagConceptEvidence":
        """Build concept evidence using existing citation conversion only."""
        citation_results = tuple(
            adapt_corpus_citation(snippet.citation)
            for snippet in retrieval_result.snippets
        )
        retrieval_status = (
            RetrievalStatus.RETRIEVED
            if retrieval_result.snippets
            else RetrievalStatus.NO_CITATION
        )
        citation_status = _citation_status(citation_results)
        return cls(
            retrieval_result=retrieval_result,
            query_terms=query_terms,
            visual_cue=visual_cue,
            citation_results=citation_results,
            retrieval_status=retrieval_status,
            citation_status=citation_status,
            provenance_strength=_provenance_strength(retrieval_status, citation_status),
        )


@dataclass(frozen=True, slots=True)
class RagVisualConceptCard:
    """RAG concept card data before prompt-generation adaptation."""

    concept_card_id: str
    rag_parent_candidate_id: str
    concept_family: VisualConceptFamily | None
    descriptor_terms: tuple[str, ...]
    material_terms: tuple[str, ...]
    context_terms: tuple[str, ...]
    source_citation_ids: tuple[str, ...]
    raw_retrieved_sentence: str
    visual_cue: VisualCue
    retrieval_score: float
    provenance_strength: str


def rank_concept_cards(
    cards: tuple[RagVisualConceptCard, ...],
    limit: int,
) -> tuple[RagVisualConceptCard, ...]:
    """Rank cards with score ordering and concept-family diversity."""
    ordered = _deduplicated_cards(tuple(sorted(cards, key=_ranking_key)))
    selected: list[RagVisualConceptCard] = []
    seen_families: set[VisualConceptFamily | None] = set()
    for card in ordered:
        if len(selected) == limit:
            return tuple(selected)
        if card.concept_family not in seen_families:
            selected.append(card)
            seen_families.add(card.concept_family)
    for card in ordered:
        if len(selected) == limit:
            return tuple(selected)
        if card not in selected:
            selected.append(card)
    return tuple(selected)


def _citation_status(
    citation_results: tuple[CitationAdapterResult, ...],
) -> CitationStatus:
    if not citation_results:
        return CitationStatus.NO_CITATION
    if all(isinstance(result, ExportCitation) for result in citation_results):
        return CitationStatus.EXPORTABLE
    return CitationStatus.NON_EXPORTABLE


def _provenance_strength(
    retrieval_status: RetrievalStatus,
    citation_status: CitationStatus,
) -> str:
    if (
        retrieval_status is RetrievalStatus.RETRIEVED
        and citation_status is CitationStatus.EXPORTABLE
    ):
        return "strong"
    return "weak"


def _ranking_key(card: RagVisualConceptCard) -> tuple[float, float, str]:
    return (-card.visual_cue.confidence, -card.retrieval_score, card.concept_card_id)


def _deduplicated_cards(
    cards: tuple[RagVisualConceptCard, ...],
) -> tuple[RagVisualConceptCard, ...]:
    selected: dict[
        tuple[
            VisualConceptFamily | None,
            tuple[str, ...],
            tuple[str, ...],
            tuple[str, ...],
            tuple[str, ...],
        ],
        RagVisualConceptCard,
    ] = {}
    for card in cards:
        _ = selected.setdefault(_prompt_signature(card), card)
    return tuple(selected.values())


def _prompt_signature(
    card: RagVisualConceptCard,
) -> tuple[
    VisualConceptFamily | None,
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    cue = card.visual_cue
    return (
        card.concept_family,
        card.descriptor_terms,
        card.material_terms,
        card.context_terms,
        (
            cue.color_bucket.value,
            cue.morphology.value,
            cue.texture_proxy.value,
            cue.size_class.value,
        ),
    )
