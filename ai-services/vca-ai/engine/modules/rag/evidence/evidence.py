"""Structured relation evidence builders for shared bridge contracts."""

from dataclasses import dataclass

from modules.prompt_generating import validate_executable_prompt
from modules.rag.evidence.citations import ExportCitation, to_export_citation_bridge
from modules.rag.evidence.concept_cards import RagVisualConceptCard
from modules.rag.evidence.prompt_adapter import render_rag_prompt_variants
from modules.shared import (
    ExportCitationBridge,
    HybridDescriptor,
    QwenBridgeResult,
    QwenBridgeStatus,
    RagAccountingRow,
    RelationAuthorityInput,
)


@dataclass(frozen=True, slots=True)
class RelationEvidenceLinks:
    """Structured relation anchors supplied by orchestration/grouping."""

    geometry_metric_ids: tuple[str, ...]
    same_object_ids: tuple[str, ...]
    source_view_ids: tuple[str, ...]
    duplicate_suppression_key: str


def build_hybrid_descriptor(
    cards: tuple[RagVisualConceptCard, ...],
    qwen_bridge: QwenBridgeResult,
    export_citations: tuple[ExportCitation, ...],
) -> HybridDescriptor:
    """Build the shared hybrid descriptor from structured visual evidence."""
    return HybridDescriptor(
        candidate_id=qwen_bridge.candidate_id,
        concept_family=_concept_family(cards),
        visual_descriptor_tokens=_visual_tokens(cards),
        qwen_selected_terms=_safe_qwen_terms(qwen_bridge.selected_terms),
        qwen_extracted_descriptors=_safe_qwen_terms(
            qwen_bridge.extracted_descriptors
        ),
        concept_card_ids=tuple(card.concept_card_id for card in cards),
        export_citations=_export_bridges(export_citations),
        source_cue_ids=tuple(f"{card.concept_card_id}:visual-cue" for card in cards),
        provenance_strength=_provenance_strength(cards, export_citations),
        evidence_flags=_evidence_flags(qwen_bridge),
    )


def build_relation_authority_input(
    accounting_row: RagAccountingRow,
    hybrid_descriptor: HybridDescriptor,
    links: RelationEvidenceLinks,
) -> RelationAuthorityInput:
    """Build the shared relation-authority input from terminal RAG evidence."""
    return RelationAuthorityInput(
        candidate_id=accounting_row.candidate_id,
        hybrid_descriptor=hybrid_descriptor,
        geometry_metric_ids=links.geometry_metric_ids,
        same_object_ids=links.same_object_ids,
        source_view_ids=links.source_view_ids,
        duplicate_suppression_key=links.duplicate_suppression_key,
        concept_family_compatible=True,
        descriptor_compatible=True,
        citation_provenance_strength=hybrid_descriptor.provenance_strength,
        rag_status=accounting_row.status,
        evidence_flags=hybrid_descriptor.evidence_flags,
    )


def _concept_family(cards: tuple[RagVisualConceptCard, ...]) -> str:
    for card in cards:
        family = card.concept_family
        if family is not None:
            return family.value
    return "unknown_visual_anomaly"


def _visual_tokens(cards: tuple[RagVisualConceptCard, ...]) -> tuple[str, ...]:
    terms: list[str] = []
    for card in cards:
        for variant in render_rag_prompt_variants(card):
            terms.extend(variant.metadata.source_terms)
    return tuple(dict.fromkeys(terms))


def _safe_qwen_terms(terms: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(validate_executable_prompt(term) for term in terms)


def _export_bridges(
    export_citations: tuple[ExportCitation, ...],
) -> tuple[ExportCitationBridge, ...]:
    return tuple(
        to_export_citation_bridge(citation) for citation in export_citations
    )


def _provenance_strength(
    cards: tuple[RagVisualConceptCard, ...],
    export_citations: tuple[ExportCitation, ...],
) -> str:
    if export_citations and all(card.provenance_strength == "strong" for card in cards):
        return "strong"
    return "weak"


def _evidence_flags(qwen_bridge: QwenBridgeResult) -> tuple[str, ...]:
    match qwen_bridge.status:
        case QwenBridgeStatus.SUCCESS:
            return ("structured_only", "qwen_success")
        case QwenBridgeStatus.FAILED:
            return ("structured_only", "qwen_failed")
