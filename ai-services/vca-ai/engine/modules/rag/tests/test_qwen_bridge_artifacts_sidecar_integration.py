from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from modules.rag.evidence.prompt_adapter import render_rag_prompt_variants
from modules.rag.operations.candidate_sidecars import (
    CandidateRagSidecarInputs,
    build_candidate_rag_sidecars,
)
from modules.rag.qwen.qwen_bridge_artifacts import write_qwen_bridge_results
from modules.shared import (
    CandidateId,
    ContractValidationError,
    QwenBridgeResult,
    QwenBridgeStatus,
)

from .qwen_bridge_artifact_test_support import (
    failed_bridge,
    make_qwen_sidecar_inputs,
    qwen_artifact,
    successful_bridge,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_successful_run_local_qwen_bridge_emits_safe_card_without_explicit_cue(
    tmp_path: Path,
) -> None:
    # Given: retrieval artifacts and a successful Qwen bridge row in this RAG run.
    run_directory = tmp_path / "output" / "rag" / "run-001"
    artifact_path = write_qwen_bridge_results(
        run_directory,
        (
            qwen_artifact(
                "lane-a/image-001-object-02/owlv2_sam2/records.json",
                0,
                successful_bridge(),
            ),
        ),
    )
    inputs = make_qwen_sidecar_inputs(tmp_path, artifact_path)

    # When: RAG joins the run-local artifact by the rough candidate id.
    result = build_candidate_rag_sidecars(inputs)

    # Then: the run-local artifact drives Qwen-derived safe card terms.
    assert len(result.evidence_rows) == 1
    assert len(result.cards) == 1
    card = result.cards[0]
    assert card.rag_parent_candidate_id == "candidate-001"
    assert card.descriptor_terms == ("white", "powder", "powdery")
    assert card.visual_cue.reasons == ("white", "powder", "powdery")
    prompts = tuple(
        variant.generated_prompt for variant in render_rag_prompt_variants(card)
    )
    assert "diagnos" not in " ".join(card.descriptor_terms)
    assert "ignore" not in " ".join(card.visual_cue.reasons)
    assert all("ignore" not in prompt for prompt in prompts)
    assert all("diagnos" not in prompt for prompt in prompts)


def test_failed_run_local_qwen_bridge_preserves_pre_qwen_card(
    tmp_path: Path,
) -> None:
    # Given: retrieval artifacts and a failed Qwen bridge row for the same candidate.
    run_directory = tmp_path / "output" / "rag" / "run-001"
    artifact_path = write_qwen_bridge_results(
        run_directory,
        (
            qwen_artifact(
                "lane-a/image-001-object-02/owlv2_sam2/records.json",
                0,
                failed_bridge(CandidateId("candidate-001")),
            ),
        ),
    )
    inputs = make_qwen_sidecar_inputs(tmp_path, artifact_path)

    # When: RAG joins the failed run-local Qwen artifact.
    result = build_candidate_rag_sidecars(inputs)

    # Then: Qwen failure does not remove retrieval-only card behavior.
    assert len(result.evidence_rows) == 1
    assert result.evidence_rows[0].rag_parent_candidate_id == "candidate-001"
    assert len(result.cards) == 1


def test_qwen_query_fields_exclude_anomaly_class_terms_and_require_safe_remainder(
) -> None:
    # Given: Qwen output that mixes anomaly labels with visual terms.
    result = QwenBridgeResult(
        candidate_id=CandidateId("candidate-001"),
        status=QwenBridgeStatus.SUCCESS,
        selected_terms=("deposit", "white crust"),
        extracted_descriptors=("corrosion", "powdery"),
        confidence=0.91,
        reason="visual observation",
        qwen_observation_id="qwen-001",
        input_view_hashes=("a" * 64,),
    )

    # When: the bridge record crosses the query-driving boundary.
    # Then: class labels cannot drive queries while safe visual terms remain.
    assert result.selected_terms == ("white crust",)
    assert result.extracted_descriptors == ("powdery",)
    with pytest.raises(ContractValidationError, match="qwen_query_fields"):
        _ = QwenBridgeResult(
            candidate_id=CandidateId("candidate-002"),
            status=QwenBridgeStatus.SUCCESS,
            selected_terms=("crack",),
            extracted_descriptors=("corrosion",),
            confidence=0.91,
            reason="visual observation",
            qwen_observation_id="qwen-002",
            input_view_hashes=("b" * 64,),
        )


def test_mapping_and_artifact_qwen_sources_emit_equivalent_cards(
    tmp_path: Path,
) -> None:
    # Given: equivalent direct and serialized Qwen bridge sources.
    artifact_path = write_qwen_bridge_results(
        tmp_path / "rag-run",
        (
            qwen_artifact(
                "lane-a/image-001-object-02/owlv2_sam2/records.json",
                0,
                successful_bridge(),
            ),
        ),
    )
    artifact_inputs = make_qwen_sidecar_inputs(tmp_path / "artifact", artifact_path)
    direct_inputs = CandidateRagSidecarInputs(
        rough_records_root=artifact_inputs.rough_records_root,
        queries_path=artifact_inputs.queries_path,
        prompt_rag_results_path=artifact_inputs.prompt_rag_results_path,
        qwen_results={CandidateId("candidate-001"): successful_bridge()},
    )

    # When: both inputs construct candidate cards.
    # Then: artifact transport does not alter downstream cues or cards.
    direct_cards = build_candidate_rag_sidecars(direct_inputs).cards
    artifact_cards = build_candidate_rag_sidecars(artifact_inputs).cards
    assert direct_cards == artifact_cards
