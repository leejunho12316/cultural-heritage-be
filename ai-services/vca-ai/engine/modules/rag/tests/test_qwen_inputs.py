from __future__ import annotations

from modules.rag.qwen.qwen_inputs import qwen_bridge_query_terms, qwen_rag_query_terms
from modules.shared import CandidateId, QwenBridgeResult, QwenBridgeStatus


def _successful_qwen_bridge() -> QwenBridgeResult:
    return QwenBridgeResult(
        candidate_id=CandidateId("candidate-001"),
        status=QwenBridgeStatus.SUCCESS,
        selected_terms=("brown region", "edge stain"),
        extracted_descriptors=("irregular", "matte"),
        confidence=0.84,
        reason="Both input views show the same localized visual feature.",
        qwen_observation_id="qwen-observation-001",
        input_view_hashes=("a" * 64,),
    )


def _failed_qwen_bridge() -> QwenBridgeResult:
    return QwenBridgeResult(
        candidate_id=CandidateId("candidate-002"),
        status=QwenBridgeStatus.FAILED,
        selected_terms=(),
        extracted_descriptors=(),
        confidence=None,
        reason="qwen backend unavailable",
        qwen_observation_id=None,
        input_view_hashes=("b" * 64,),
        failure_code="qwen_backend_unavailable",
    )


def test_qwen_rag_query_input_uses_only_selected_terms_and_descriptors() -> None:
    # Given: a successful Qwen bridge with query and provenance fields.
    bridge = _successful_qwen_bridge()
    rag_input = bridge.to_rag_query_input()

    # When: RAG consumes the query-only handoff.
    terms = qwen_rag_query_terms(rag_input)

    # Then: only selected terms and descriptors become retrieval tokens.
    assert terms.lexical_tokens == (
        "brown region",
        "edge",
        "irregular",
        "matte",
    )
    assert bridge.reason not in terms.lexical_tokens
    assert bridge.qwen_observation_id not in terms.lexical_tokens
    assert bridge.input_view_hashes[0] not in terms.lexical_tokens


def test_qwen_bridge_consumer_uses_shared_query_driving_seam() -> None:
    # Given: a QwenBridgeResult exposing a dedicated query-driving seam.
    bridge = _successful_qwen_bridge()

    # When: RAG consumes the shared bridge directly.
    terms = qwen_bridge_query_terms(bridge)

    # Then: the result matches the shared query-only handoff exactly.
    assert terms == qwen_rag_query_terms(bridge.to_rag_query_input())


def test_failed_qwen_bridge_does_not_invent_query_terms() -> None:
    # Given: a failed Qwen handoff with failure provenance but no query evidence.
    bridge = _failed_qwen_bridge()

    # When: RAG consumes the failed bridge.
    terms = qwen_bridge_query_terms(bridge)

    # Then: no descriptors or fallback terms are invented from failure metadata.
    assert terms is None
    assert bridge.reason == "qwen backend unavailable"
    assert bridge.failure_code == "qwen_backend_unavailable"


def test_qwen_query_consumer_exposes_no_prompt_text() -> None:
    # Given: query terms derived from Qwen's query-only handoff.
    terms = qwen_bridge_query_terms(_successful_qwen_bridge())

    # When/Then: the RAG consumer returns retrieval terms, not executable prompt text.
    assert terms is not None
    assert not hasattr(terms, "prompt_text")
