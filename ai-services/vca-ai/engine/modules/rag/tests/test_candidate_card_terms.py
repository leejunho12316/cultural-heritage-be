from __future__ import annotations

from modules.rag.operations.candidate_card_terms import qwen_query_signature
from modules.shared import CandidateId, QwenBridgeResult, QwenBridgeStatus


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
