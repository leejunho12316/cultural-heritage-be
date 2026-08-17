from __future__ import annotations

from modules.rag.operations.candidate_card_terms import (
    provenance_strength_for_result,
    qwen_query_signature,
)
from modules.rag.operations.candidate_sidecar_artifacts import PromptRagResultRecord
from modules.shared import CandidateId, QwenBridgeResult, QwenBridgeStatus


def _result(**overrides: object) -> PromptRagResultRecord:
    defaults: dict[str, object] = {
        "query_id": "owlv2_sam2:prompt-0001",
        "lane": "owlv2_sam2",
        "prompt_text": "spalled surface",
        "citation_id": "fixture:chunk-0001:citation",
        "chunk_id": "fixture:chunk-0001",
        "score": 0.9,
        "snippet_text": "fixture snippet text",
        "matched_terms": ("spalled", "surface"),
        "rank": 1,
    }
    defaults.update(overrides)
    return PromptRagResultRecord(**defaults)  # type: ignore[arg-type]


def _bridge(candidate_id: str, **overrides: object) -> QwenBridgeResult:
    defaults: dict[str, object] = {
        "candidate_id": CandidateId(candidate_id),
        "status": QwenBridgeStatus.SUCCESS,
        "selected_terms": ("white powder",),
        "extracted_descriptors": ("powdery",),
        "confidence": 0.9,
        "reason": "fixture bridge result",
        "qwen_observation_id": "qwen-obs-001",
        "input_view_hashes": ("viewhash-001",),
    }
    defaults.update(overrides)
    return QwenBridgeResult(**defaults)  # type: ignore[arg-type]


def test_successful_bridge_yields_sorted_deduplicated_signature() -> None:
    candidate_id = CandidateId("candidate-001")
    bridge = _bridge(str(candidate_id))
    signature = qwen_query_signature(candidate_id, {candidate_id: bridge})
    assert signature == ("powdery", "white powder")


def test_missing_candidate_yields_empty_signature() -> None:
    candidate_id = CandidateId("candidate-002")
    signature = qwen_query_signature(candidate_id, {})
    assert signature == ()


def test_failed_bridge_yields_empty_signature() -> None:
    candidate_id = CandidateId("candidate-003")
    bridge = _bridge(
        str(candidate_id),
        status=QwenBridgeStatus.FAILED,
        selected_terms=(),
        extracted_descriptors=(),
        confidence=None,
        qwen_observation_id=None,
        failure_code="qwen_backend_unavailable",
    )
    signature = qwen_query_signature(candidate_id, {candidate_id: bridge})
    assert signature == ()


def test_duplicate_terms_across_fields_are_deduplicated() -> None:
    candidate_id = CandidateId("candidate-004")
    bridge = _bridge(
        str(candidate_id),
        selected_terms=("rough",),
        extracted_descriptors=("rough",),
    )
    signature = qwen_query_signature(candidate_id, {candidate_id: bridge})
    assert signature == ("rough",)


def test_provenance_strength_is_strong_when_score_and_terms_both_clear_threshold() -> (
    None
):
    result = _result(score=0.9, matched_terms=("spalled", "surface", "crust"))
    assert provenance_strength_for_result(result) == "strong"


def test_provenance_strength_is_weak_when_score_clears_but_terms_do_not() -> None:
    result = _result(score=0.9, matched_terms=("spalled",))
    assert provenance_strength_for_result(result) == "weak"


def test_provenance_strength_is_weak_when_terms_clear_but_score_does_not() -> None:
    result = _result(score=0.5, matched_terms=("spalled", "surface"))
    assert provenance_strength_for_result(result) == "weak"


def test_provenance_strength_is_weak_when_neither_clears_threshold() -> None:
    result = _result(score=0.3, matched_terms=())
    assert provenance_strength_for_result(result) == "weak"


def test_provenance_strength_is_strong_exactly_at_threshold() -> None:
    result = _result(score=0.75, matched_terms=("spalled", "surface"))
    assert provenance_strength_for_result(result) == "strong"
