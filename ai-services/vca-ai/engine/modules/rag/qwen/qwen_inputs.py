"""Qwen query-only handoff consumption for RAG retrieval."""

from modules.rag.retrieval.terms import QueryTerms, query_terms
from modules.shared import QwenBridgeResult, QwenBridgeStatus, QwenRagQueryInput


# Qwen이 넘긴 selected_terms/extracted_descriptors만으로 QueryTerms를 만든다.
# 관찰된 코퍼스 원문 용어(observed_terms)는 비워둬, Qwen 출력이 코퍼스 근거로
# 둔갑하지 않게 한다.
def qwen_rag_query_terms(query_input: QwenRagQueryInput) -> QueryTerms:
    """Convert Qwen's query-only handoff into retrieval query terms."""
    return query_terms(
        observed_terms=(),
        english_terms=query_input.selected_terms + query_input.extracted_descriptors,
    )


# QwenBridgeResult를 검색 쿼리 용어로 변환하는 진입점. 실패 상태면 None을
# 돌려줘 호출부가 Qwen 없이 진행하도록 한다.
def qwen_bridge_query_terms(bridge: QwenBridgeResult) -> QueryTerms | None:
    """Consume shared Qwen bridge evidence without provenance text leakage."""
    match bridge.status:
        case QwenBridgeStatus.SUCCESS:
            return qwen_rag_query_terms(bridge.to_rag_query_input())
        case QwenBridgeStatus.FAILED:
            return None
