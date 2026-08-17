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
    # RetrievalResult와 시각 단서(visual cue)를 결합해 근거(evidence) 레코드를
    # 만든다. RagVisualConceptCard를 만들기 전 단계에서 candidate_sidecars가
    # 호출한다.
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
    image_id: str
    concept_family: VisualConceptFamily | None
    descriptor_terms: tuple[str, ...]
    material_terms: tuple[str, ...]
    context_terms: tuple[str, ...]
    source_citation_ids: tuple[str, ...]
    raw_retrieved_sentence: str
    visual_cue: VisualCue
    retrieval_score: float
    provenance_strength: str


# 점수순 정렬 + concept_family 다양성을 고려해 카드 limit개를 선택한다.
# candidate_sidecars.build_candidate_rag_sidecars가 최종 카드 목록을 만들 때
# 호출한다. family별로 한 장씩 먼저 채운 뒤, 남은 자리를 점수순으로 채운다.
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


# 인용 목록이 전부 내보내기 가능(ExportCitation)해야만 EXPORTABLE로 판정한다.
def _citation_status(
    citation_results: tuple[CitationAdapterResult, ...],
) -> CitationStatus:
    if not citation_results:
        return CitationStatus.NO_CITATION
    if all(isinstance(result, ExportCitation) for result in citation_results):
        return CitationStatus.EXPORTABLE
    return CitationStatus.NON_EXPORTABLE


# retrieval/citation 상태가 모두 최상일 때만 "strong", 그 외는 전부 "weak"로
# 단순화한다.
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


# 신뢰도 → 검색 점수 → id 순으로 내림차순 정렬하기 위한 정렬 키.
def _ranking_key(card: RagVisualConceptCard) -> tuple[float, float, str]:
    return (-card.visual_cue.confidence, -card.retrieval_score, card.concept_card_id)


# 정렬된 카드 목록에서 동일 시그니처(_prompt_signature)의 첫 카드만 남긴다.
# rank_concept_cards가 family 다양성 선택 전에 먼저 호출한다.
def _deduplicated_cards(
    cards: tuple[RagVisualConceptCard, ...],
) -> tuple[RagVisualConceptCard, ...]:
    # 시그니처는 image_id로 범위가 한정된다: 서로 다른 이미지의 카드 두 장이
    # 같은 서술 어휘를 쓴다는 이유만으로 하나로 합쳐지면 안 된다(허용된
    # descriptor/family 용어가 좁아서 이런 충돌이 흔하다) - 그렇게 되면 실제로
    # 존재하는, 서로 다른 이상 소견의 개념 카드가 조용히 사라진다.
    selected: dict[
        tuple[
            str,
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


# 카드 두 장이 "같은 소견"인지 판단하는 동일성 키를 만든다. image_id로 범위가
# 한정되어 있어 서로 다른 이미지의 카드는 절대 합쳐지지 않는다.
def _prompt_signature(
    card: RagVisualConceptCard,
) -> tuple[
    str,
    VisualConceptFamily | None,
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    cue = card.visual_cue
    return (
        card.image_id,
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
