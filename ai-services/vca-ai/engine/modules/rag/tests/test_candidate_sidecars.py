from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from modules.rag.operations.candidate_sidecar_artifacts import read_results
from modules.rag.operations.candidate_sidecars import (
    RAG_CANDIDATE_EVIDENCE_SIDECAR,
    RAG_CANDIDATE_SIDECAR_MANIFEST,
    RAG_EVIDENCE_READY,
    RAG_VISUAL_CONCEPT_CARDS_SIDECAR,
    CandidateRagSidecarInputs,
    build_candidate_rag_sidecars,
    write_candidate_rag_sidecars,
)
from modules.rag.qwen import (
    QwenBridgeCandidateArtifact,
    write_qwen_bridge_results,
)
from modules.shared import (
    CandidateId,
    ContractValidationError,
    PathSafetyError,
    QwenBridgeResult,
    QwenBridgeStatus,
)

from .candidate_sidecar_test_support import (
    first_candidate_id,
    make_candidate_sidecar_inputs,
    successful_qwen_result,
    visual_cue,
)

if TYPE_CHECKING:
    from pathlib import Path

def test_exact_lane_prompt_join_creates_one_evidence_row_per_rough_candidate(
    tmp_path: Path,
) -> None:
    # Given: two rough candidates and duplicate prompt text in a different lane.
    inputs = make_candidate_sidecar_inputs(tmp_path)

    # When: sidecars are built without current-run Qwen inputs.
    result = build_candidate_rag_sidecars(inputs)

    # Then: every rough candidate is accounted by exact lane+prompt retrieval.
    assert len(result.evidence_rows) == 2
    first = result.evidence_rows[0]
    assert first.lane == "lane-a"
    assert first.prompt_text == "white deposit on rim"
    assert first.query_id == "lane-a:prompt-01"
    assert first.matched_citation_ids == ("citation-001",)
    assert first.matched_chunk_ids == ("chunk-001",)
    assert first.top_citation_id == "citation-001"
    assert first.top_chunk_id == "chunk-001"
    assert first.top_result_rank == 1
    assert first.top_retrieval_score == 7.5
    assert first.evidence_state == RAG_EVIDENCE_READY
    assert first.evidence_reason is None
    assert "lane-b" not in first.rag_parent_candidate_id


def test_missing_qwen_emits_deterministic_pre_qwen_card_from_safe_retrieval_terms(
    tmp_path: Path,
) -> None:
    # Given: rough/retrieval artifacts with no explicit QwenBridgeResult inputs.
    inputs = make_candidate_sidecar_inputs(tmp_path)

    # When: candidate mapping is generated.
    result = build_candidate_rag_sidecars(inputs)

    # Then: RAG evidence is ready and safe retrieval terms create a pre-Qwen card.
    row = result.evidence_rows[0]
    assert row.evidence_state == RAG_EVIDENCE_READY
    assert row.evidence_reason is None
    assert not hasattr(row, "qwen_status")
    assert not hasattr(row, "status")
    assert len(result.cards) == 1
    assert result.cards[0].descriptor_terms == ("white",)
    assert result.cards[0].visual_cue.reasons == ("white",)
    assert result.cards[0].material_terms == ()


def test_successful_qwen_without_visual_cue_uses_qwen_derived_card(
    tmp_path: Path,
) -> None:
    # Given: successful explicit QwenBridgeResult but no VisualCue.
    inputs = make_candidate_sidecar_inputs(tmp_path)
    candidate_id = first_candidate_id(inputs)
    with_qwen = CandidateRagSidecarInputs(
        rough_records_root=inputs.rough_records_root,
        queries_path=inputs.queries_path,
        prompt_rag_results_path=inputs.prompt_rag_results_path,
        qwen_results={
            CandidateId(candidate_id): successful_qwen_result(CandidateId(candidate_id))
        },
    )

    # When: candidate mapping is generated.
    result = build_candidate_rag_sidecars(with_qwen)

    # Then: safe Qwen terms drive the card cue.
    row = result.evidence_rows[0]
    assert row.evidence_state == RAG_EVIDENCE_READY
    assert row.evidence_reason is None
    assert not hasattr(row, "visual_cue_summary")
    assert len(result.cards) == 1
    assert result.cards[0].descriptor_terms == ("white", "powder", "powdery")
    assert result.cards[0].visual_cue.reasons == ("white", "powder", "powdery")


def test_explicit_visual_cue_overrides_qwen_derived_cue(tmp_path: Path) -> None:
    # Given: the same candidate has Qwen terms and an explicit visual cue.
    inputs = make_candidate_sidecar_inputs(tmp_path)
    candidate_id = first_candidate_id(inputs)
    candidate = CandidateId(candidate_id)
    with_qwen_and_cue = CandidateRagSidecarInputs(
        rough_records_root=inputs.rough_records_root,
        queries_path=inputs.queries_path,
        prompt_rag_results_path=inputs.prompt_rag_results_path,
        qwen_results={candidate: successful_qwen_result(candidate)},
        visual_cues={candidate: visual_cue()},
    )

    # When: candidate mapping is generated.
    result = build_candidate_rag_sidecars(with_qwen_and_cue)

    # Then: explicit visual cue fields win over Qwen-derived defaults.
    assert len(result.cards) == 1
    assert result.cards[0].visual_cue.reasons == ("white", "crust", "powdery")
    assert result.cards[0].descriptor_terms == ("white", "crust", "powdery")


def test_directory_qwen_bridge_artifact_is_resolved_by_sidecar_inputs(
    tmp_path: Path,
) -> None:
    # Given: a directory-backed Qwen bridge artifact for the candidate.
    inputs = make_candidate_sidecar_inputs(tmp_path)
    candidate_id = first_candidate_id(inputs)
    artifact_path = write_qwen_bridge_results(
        tmp_path / "rag-run",
        (
            QwenBridgeCandidateArtifact(
                "lane-a/image-001-object-02/owlv2_sam2/records.json",
                0,
                QwenBridgeResult(
                    candidate_id=CandidateId(candidate_id),
                    status=QwenBridgeStatus.SUCCESS,
                    selected_terms=("white powder",),
                    extracted_descriptors=("powdery",),
                    confidence=0.91,
                    reason="explicit bridge result",
                    qwen_observation_id="qwen-001",
                    input_view_hashes=("viewhash-001",),
                ),
            ),
        ),
    )
    with_artifact = CandidateRagSidecarInputs(
        rough_records_root=inputs.rough_records_root,
        queries_path=inputs.queries_path,
        prompt_rag_results_path=inputs.prompt_rag_results_path,
        qwen_bridge_results_path=artifact_path,
    )

    # When: candidate mapping is generated.
    result = build_candidate_rag_sidecars(with_artifact)

    # Then: the directory artifact drives the same Qwen-derived cue.
    assert artifact_path == tmp_path / "rag-run" / "qwen_bridge_results"
    assert len(result.cards) == 1
    assert result.cards[0].descriptor_terms == ("white", "powder", "powdery")
    assert result.cards[0].visual_cue.reasons == ("white", "powder", "powdery")


def test_explicit_visual_cue_emit_card_jsonl_without_qwen(tmp_path: Path) -> None:
    # Given: explicit VisualCue inputs and no Qwen dependency.
    inputs = make_candidate_sidecar_inputs(tmp_path)
    candidate_id = first_candidate_id(inputs)
    candidate = CandidateId(candidate_id)
    with_card_inputs = CandidateRagSidecarInputs(
        rough_records_root=inputs.rough_records_root,
        queries_path=inputs.queries_path,
        prompt_rag_results_path=inputs.prompt_rag_results_path,
        visual_cues={candidate: visual_cue()},
    )

    # When: sidecars are built and written as JSONL plus a manifest.
    result = build_candidate_rag_sidecars(with_card_inputs)
    write_candidate_rag_sidecars(tmp_path / "sidecars", result)

    # Then: card emission is additional to the pre-Qwen evidence sidecar.
    row = result.evidence_rows[0]
    assert row.evidence_state == RAG_EVIDENCE_READY
    assert len(result.cards) == 1
    assert result.cards[0].rag_parent_candidate_id == candidate_id
    assert result.cards[0].concept_family is not None
    assert result.cards[0].concept_family.value == "deposit"
    assert "deposit" not in result.cards[0].descriptor_terms
    assert result.cards[0].material_terms == ()
    assert result.cards[0].context_terms == ("surface",)
    card_rows = (tmp_path / "sidecars" / RAG_VISUAL_CONCEPT_CARDS_SIDECAR).read_text(
        encoding="utf-8"
    ).splitlines()
    evidence_rows = (tmp_path / "sidecars" / RAG_CANDIDATE_EVIDENCE_SIDECAR).read_text(
        encoding="utf-8"
    ).splitlines()
    manifest = (tmp_path / "sidecars" / RAG_CANDIDATE_SIDECAR_MANIFEST).read_text(
        encoding="utf-8"
    )
    assert len(card_rows) == 1
    assert len(evidence_rows) == 2
    assert manifest == (
        '{"rag_candidate_evidence_rows":2,"rag_visual_concept_cards":1,'
        '"schema":"rag_candidate_evidence_v1"}'
    )


def test_card_jsonl_can_be_empty_while_evidence_sidecar_is_non_empty(
    tmp_path: Path,
) -> None:
    # Given: current-run artifacts without explicit QwenBridgeResult or VisualCue.
    inputs = make_candidate_sidecar_inputs(tmp_path)

    # When: sidecars are written.
    result = build_candidate_rag_sidecars(inputs)
    write_candidate_rag_sidecars(tmp_path / "sidecars", result)

    # Then: evidence and safe pre-Qwen cards are both persisted.
    assert (tmp_path / "sidecars" / RAG_CANDIDATE_EVIDENCE_SIDECAR).read_text(
        encoding="utf-8"
    ).strip()
    assert (tmp_path / "sidecars" / RAG_VISUAL_CONCEPT_CARDS_SIDECAR).read_text(
        encoding="utf-8"
    ).strip()


def test_missing_qwen_artifact_raises_typed_contract_error(
    tmp_path: Path,
) -> None:
    # Given: an otherwise valid sidecar input with an explicitly supplied missing path.
    inputs = make_candidate_sidecar_inputs(tmp_path)
    missing_artifact = tmp_path / "missing-qwen.jsonl"
    with_artifact = CandidateRagSidecarInputs(
        rough_records_root=inputs.rough_records_root,
        queries_path=inputs.queries_path,
        prompt_rag_results_path=inputs.prompt_rag_results_path,
        qwen_bridge_results_path=missing_artifact,
    )

    # When: the optional artifact is resolved.
    # Then: explicit absence is a typed contract failure, not implicit no-Qwen behavior.
    with pytest.raises(ContractValidationError, match="qwen_bridge_results_path"):
        _ = build_candidate_rag_sidecars(with_artifact)


def test_writer_rejects_source_document_output(tmp_path: Path) -> None:
    # Given: a source corpus root and a generated sidecar result.
    result = build_candidate_rag_sidecars(make_candidate_sidecar_inputs(tmp_path))
    source_root = tmp_path / "source-documents"
    source_root.mkdir()

    # When: a sidecar write targets the source corpus.
    # Then: shared write-target protection rejects it before writing.
    with pytest.raises(PathSafetyError):
        write_candidate_rag_sidecars(
            source_root / "sidecars",
            result,
            source_document_root=source_root,
        )


def test_reader_rejects_json_with_trailing_content(tmp_path: Path) -> None:
    # Given: a result row that otherwise has a complete matching schema.
    results_path = tmp_path / "results.jsonl"
    _ = results_path.write_text(
        json.dumps(
            {
                "chunk_id": "chunk-001",
                "citation_id": "citation-001",
                "lane": "lane-a",
                "matched_terms": ["white"],
                "prompt_text": "white deposit on rim",
                "query_id": "query-001",
                "rank": 1,
                "score": 1.0,
                "snippet_text": "white",
            },
            sort_keys=True,
        )
        + " unexpected\n",
        encoding="utf-8",
    )

    # When: candidate retrieval JSONL is parsed.
    # Then: strict object parsing rejects the malformed row.
    assert read_results(results_path) == ()
