"""Qwen query-only handoff consumption for RAG retrieval."""

from modules.rag.retrieval.terms import QueryTerms, query_terms
from modules.shared import QwenBridgeResult, QwenBridgeStatus, QwenRagQueryInput


def qwen_rag_query_terms(query_input: QwenRagQueryInput) -> QueryTerms:
    """Convert Qwen's query-only handoff into retrieval query terms."""
    return query_terms(
        observed_terms=(),
        english_terms=query_input.selected_terms + query_input.extracted_descriptors,
    )


def qwen_bridge_query_terms(bridge: QwenBridgeResult) -> QueryTerms | None:
    """Consume shared Qwen bridge evidence without provenance text leakage."""
    match bridge.status:
        case QwenBridgeStatus.SUCCESS:
            return qwen_rag_query_terms(bridge.to_rag_query_input())
        case QwenBridgeStatus.FAILED:
            return None
