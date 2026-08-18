from __future__ import annotations

from modules.prompt_generating import VisualConceptFamily
from modules.rag.operations.candidate_card_terms import (
    corrected_concept_family,
    family_from_terms,
    provenance_strength_for_result,
    qwen_query_signature,
)
from modules.rag.operations.candidate_sidecar_artifacts import PromptRagResultRecord
from modules.shared import CandidateId, QwenBridgeResult, QwenBridgeStatus


def _result(rank: int, matched_terms: tuple[str, ...]) -> PromptRagResultRecord:
    return PromptRagResultRecord(
        query_id="query-001",
        lane="owlv2",
        prompt_text="surface crack",
        citation_id=f"citation-{rank:03d}",
        chunk_id=f"chunk-{rank:03d}",
        score=1.0 - rank * 0.01,
        snippet_text="fixture snippet",
        matched_terms=matched_terms,
        rank=rank,
    )


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


def test_family_from_terms_matches_a_keyword() -> None:
    assert family_from_terms(("surface deposit",)) is VisualConceptFamily.DEPOSIT


def test_family_from_terms_returns_none_without_a_match() -> None:
    assert family_from_terms(("unrelated", "words")) is None


def test_corrected_concept_family_overrides_on_majority_agreement() -> None:
    # Given: the seed prompt guessed "crack", but 3 of the top 5 retrieved
    # results consistently describe deposit evidence instead.
    results = (
        _result(1, ("deposit",)),
        _result(2, ("deposit",)),
        _result(3, ("deposit",)),
        _result(4, ("crack",)),
        _result(5, ()),
    )
    corrected = corrected_concept_family(VisualConceptFamily.CRACK, results)
    assert corrected is VisualConceptFamily.DEPOSIT


def test_corrected_concept_family_keeps_seed_without_a_majority() -> None:
    # Given: only 2 of 5 top results agree on a different family - short of
    # a majority, so a single noisy hit can't swing the correction.
    results = (
        _result(1, ("deposit",)),
        _result(2, ("deposit",)),
        _result(3, ("crack",)),
        _result(4, ()),
        _result(5, ()),
    )
    corrected = corrected_concept_family(VisualConceptFamily.CRACK, results)
    assert corrected is VisualConceptFamily.CRACK


def test_corrected_concept_family_keeps_seed_when_no_result_votes() -> None:
    results = (_result(1, ()), _result(2, ("unrelated",)))
    corrected = corrected_concept_family(VisualConceptFamily.CRACK, results)
    assert corrected is VisualConceptFamily.CRACK


def test_corrected_concept_family_only_considers_top_n_by_rank() -> None:
    # Given: results passed out of rank order, with a deposit majority
    # sitting entirely outside the top-5 window (ranks 1-5).
    results = (
        _result(6, ("deposit",)),
        _result(7, ("deposit",)),
        _result(8, ("deposit",)),
        _result(1, ("crack",)),
        _result(2, ()),
        _result(3, ()),
        _result(4, ()),
        _result(5, ()),
    )
    corrected = corrected_concept_family(VisualConceptFamily.CRACK, results)
    assert corrected is VisualConceptFamily.CRACK


def test_corrected_concept_family_empty_results_keeps_seed() -> None:
    assert corrected_concept_family(VisualConceptFamily.CRACK, ()) is (
        VisualConceptFamily.CRACK
    )


def _scored_result(score: float, matched_terms: tuple[str, ...]) -> PromptRagResultRecord:
    return PromptRagResultRecord(
        query_id="owlv2_sam2:prompt-0001",
        lane="owlv2_sam2",
        prompt_text="spalled surface",
        citation_id="fixture:chunk-0001:citation",
        chunk_id="fixture:chunk-0001",
        score=score,
        snippet_text="fixture snippet text",
        matched_terms=matched_terms,
        rank=1,
    )


def test_provenance_strength_is_strong_when_score_and_terms_both_clear_threshold() -> (
    None
):
    result = _scored_result(0.9, ("spalled", "surface", "crust"))
    assert provenance_strength_for_result(result) == "strong"


def test_provenance_strength_is_weak_when_score_clears_but_terms_do_not() -> None:
    result = _scored_result(0.9, ("spalled",))
    assert provenance_strength_for_result(result) == "weak"


def test_provenance_strength_is_weak_when_terms_clear_but_score_does_not() -> None:
    result = _scored_result(0.5, ("spalled", "surface"))
    assert provenance_strength_for_result(result) == "weak"


def test_provenance_strength_is_weak_when_neither_clears_threshold() -> None:
    result = _scored_result(0.3, ())
    assert provenance_strength_for_result(result) == "weak"


def test_provenance_strength_is_strong_exactly_at_threshold() -> None:
    result = _scored_result(0.75, ("spalled", "surface"))
    assert provenance_strength_for_result(result) == "strong"
