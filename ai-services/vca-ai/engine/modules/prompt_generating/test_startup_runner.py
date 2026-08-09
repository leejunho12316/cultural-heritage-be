from __future__ import annotations

import json
from typing import TYPE_CHECKING

from modules.mask_refining.execution.prompts import read_prompt_variants
from modules.orchestration.stage_execution import ProjectStageRequest
from modules.orchestration.stage_paths import StagePathMap
from modules.prompt_generating.startup_runner import run_prompt_generating_stage
from modules.rag.operations.candidate_sidecar_models import (
    RAG_VISUAL_CONCEPT_CARDS_SIDECAR,
)
from modules.rag.qwen.qwen_bridge_json import parse_json_object
from modules.shared import ExitCode, RagLane

if TYPE_CHECKING:
    from pathlib import Path


def _stage_paths(tmp_path: Path) -> StagePathMap:
    return StagePathMap(
        preprocessing=tmp_path / "preprocessing",
        rough_masking=tmp_path / "rough_masking",
        visual_cue_generation=tmp_path / "visual_cue_generation",
        rag=tmp_path / "rag",
        prompt_generating=tmp_path / "prompt_generating",
        mask_refining=tmp_path / "mask_refining",
        anomaly_grouping=tmp_path / "anomaly_grouping",
        report_generating=tmp_path / "report_generating",
    )


def _request(tmp_path: Path, *, dry_run: bool = False) -> ProjectStageRequest:
    return ProjectStageRequest(
        project_name="prompt-startup",
        stage_name="prompt_generating",
        paths=_stage_paths(tmp_path),
        device="cpu",
        model_cache_root=tmp_path / "models",
        dry_run=dry_run,
        verify_model_hashes=True,
    )


def _write_cards(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "concept_card_id": "concept-001",
        "concept_family": "deposit",
        "context_terms": ["surface"],
        "descriptor_terms": ["white", "powdery"],
        "image_id": "image-001",
        "material_terms": ["stone"],
        "provenance_strength": "strong",
        "rag_parent_candidate_id": "candidate-001",
        "raw_retrieved_sentence": "citation-only sentence",
        "retrieval_score": 7.5,
        "source_citation_ids": ["citation-001"],
        "visual_cue": {
            "boundary_relation": "interior",
            "color_bucket": "white",
            "confidence": 0.91,
            "morphology": "crust",
            "reasons": ["test fixture"],
            "size_class": "local",
            "texture_proxy": "powdery",
        },
    }
    _ = path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")


def test_run_prompt_generating_stage_writes_mask_refining_contract(
    tmp_path: Path,
) -> None:
    # Given: RAG visual concept cards with prompt-safe terms.
    request = _request(tmp_path)
    _write_cards(request.paths.rag / RAG_VISUAL_CONCEPT_CARDS_SIDECAR)

    # When: the prompt-generation startup stage executes.
    exit_code = run_prompt_generating_stage(request)

    # Then: mask_refining can read grouped lane variants from the project output.
    assert exit_code == int(ExitCode.OK)
    result = read_prompt_variants(request.paths.prompt_generating)
    assert result.manifest_schema == "rag_refinement_prompt_variants_v1"
    assert len(result.groups) == 3
    assert result.skips == ()
    assert {group.model_lane for group in result.groups} == set(RagLane)
    assert all(
        group.rag_parent_candidate_id == "candidate-001" for group in result.groups
    )
    manifest = parse_json_object(
        (request.paths.prompt_generating / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["prompt_variants"] == 3
    assert manifest["rag_visual_concept_cards"] == 1


def test_run_prompt_generating_stage_succeeds_with_zero_cards(
    tmp_path: Path,
) -> None:
    # Given: the RAG stage ran and wrote its sidecar, but found no citable
    # evidence for any candidate (file exists, zero card lines).
    request = _request(tmp_path)
    cards_path = request.paths.rag / RAG_VISUAL_CONCEPT_CARDS_SIDECAR
    cards_path.parent.mkdir(parents=True, exist_ok=True)
    _ = cards_path.write_text("", encoding="utf-8")

    # When: the prompt-generation startup stage executes.
    exit_code = run_prompt_generating_stage(request)

    # Then: it succeeds with empty mask-refining inputs rather than failing
    # the whole run - a report without RAG evidence is still a report.
    assert exit_code == int(ExitCode.OK)
    result = read_prompt_variants(request.paths.prompt_generating)
    assert result.groups == ()
    manifest = parse_json_object(
        (request.paths.prompt_generating / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["prompt_variants"] == 0
    assert manifest["rag_visual_concept_cards"] == 0


def test_run_prompt_generating_stage_fails_when_cards_are_missing(
    tmp_path: Path,
) -> None:
    # Given: startup reaches prompt generation before RAG card sidecars exist.
    request = _request(tmp_path)

    # When: the stage runner executes.
    exit_code = run_prompt_generating_stage(request)

    # Then: it fails honestly without writing mask-refining inputs.
    assert exit_code == int(ExitCode.INCOMPLETE_OR_FAILURE)
    assert not (request.paths.prompt_generating / "manifest.json").exists()
    assert not (
        request.paths.prompt_generating / "rag_refinement_prompt_variants.jsonl"
    ).exists()


def test_run_prompt_generating_stage_dry_run_uses_available_cards(
    tmp_path: Path,
) -> None:
    # Given: dry-run startup has already materialized deterministic RAG cards.
    request = _request(tmp_path, dry_run=True)
    _write_cards(request.paths.rag / RAG_VISUAL_CONCEPT_CARDS_SIDECAR)

    # When: the dry-run stage executes.
    exit_code = run_prompt_generating_stage(request)

    # Then: planned prompt artifacts are still valid downstream inputs.
    assert exit_code == int(ExitCode.OK)
    result = read_prompt_variants(request.paths.prompt_generating)
    assert len(result.groups) == 3
