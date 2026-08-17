from __future__ import annotations

import json
from typing import TYPE_CHECKING

from modules.rag.evidence.prompt_adapter import render_rag_prompt_variants
from modules.rag.operations.candidate_sidecars import (
    CandidateRagSidecarInputs,
    build_candidate_rag_sidecars,
)

from .candidate_sidecar_test_support import write_jsonl

if TYPE_CHECKING:
    from pathlib import Path


def test_image_analysis_table_caption_is_skipped_before_card_creation(
    tmp_path: Path,
) -> None:
    # Given: an image-analysis table caption appears before usable evidence.
    rough_root = tmp_path / "rough"
    records_path = rough_root / "lane-a" / "image-001-object-02" / "owlv2_sam2"
    records_path.mkdir(parents=True)
    _ = (records_path / "records.json").write_text(
        json.dumps(
            [
                {
                    "accepted": True,
                    "candidate_id": "candidate-001",
                    "image": "image-001",
                    "prompt": "white flaking on rim",
                }
            ],
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    queries_path = tmp_path / "queries.jsonl"
    results_path = tmp_path / "prompt_rag_results.jsonl"
    write_jsonl(
        queries_path,
        (
            {
                "lane": "lane-a",
                "prompt_text": "white flaking on rim",
                "query_id": "lane-a:prompt-01",
            },
        ),
    )
    write_jsonl(
        results_path,
        (
            {
                "chunk_id": "chunk-caption",
                "citation_id": "citation-caption",
                "lane": "lane-a",
                "matched_terms": ["white", "flaking"],
                "prompt_text": "white flaking on rim",
                "query_id": "lane-a:prompt-01",
                "rank": 1,
                "score": 30.0,
                "snippet_text": (
                    "VNIR Image VNIR Image Image Differencing Analysis Result "
                    "Monochrome Processing Changing Area Before Before After After"
                ),
            },
            {
                "chunk_id": "chunk-usable",
                "citation_id": "citation-usable",
                "lane": "lane-a",
                "matched_terms": ["white", "flaking"],
                "prompt_text": "white flaking on rim",
                "query_id": "lane-a:prompt-01",
                "rank": 2,
                "score": 12.0,
                "snippet_text": "white flaking edge visible on a ceramic surface",
            },
        ),
    )

    # When: sidecars select prompt-renderable evidence.
    result = build_candidate_rag_sidecars(
        CandidateRagSidecarInputs(rough_root, queries_path, results_path)
    )

    # Then: image-analysis captions cannot become card evidence.
    assert len(result.cards) == 1
    card = result.cards[0]
    assert card.source_citation_ids == ("citation-usable",)
    assert "Image Differencing" not in card.raw_retrieved_sentence


def test_area_only_context_falls_back_to_surface_without_area_surface(
    tmp_path: Path,
) -> None:
    # Given: area is the only context-like retrieval term.
    rough_root = tmp_path / "rough"
    records_path = rough_root / "lane-a" / "image-001-object-02" / "owlv2_sam2"
    records_path.mkdir(parents=True)
    _ = (records_path / "records.json").write_text(
        json.dumps(
            [
                {
                    "accepted": True,
                    "candidate_id": "candidate-001",
                    "image": "image-001",
                    "prompt": "white deposit on rim",
                }
            ],
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    queries_path = tmp_path / "queries.jsonl"
    results_path = tmp_path / "prompt_rag_results.jsonl"
    write_jsonl(
        queries_path,
        (
            {
                "lane": "lane-a",
                "prompt_text": "white deposit on rim",
                "query_id": "lane-a:prompt-01",
            },
        ),
    )
    write_jsonl(
        results_path,
        (
            {
                "chunk_id": "chunk-001",
                "citation_id": "citation-001",
                "lane": "lane-a",
                "matched_terms": ["white", "deposit", "area"],
                "prompt_text": "white deposit on rim",
                "query_id": "lane-a:prompt-01",
                "rank": 1,
                "score": 7.5,
                "snippet_text": "white deposit in the inspected area",
            },
        ),
    )

    # When: prompts are rendered from the resulting card.
    result = build_candidate_rag_sidecars(
        CandidateRagSidecarInputs(rough_root, queries_path, results_path)
    )
    prompts = tuple(
        variant.generated_prompt
        for variant in render_rag_prompt_variants(result.cards[0])
    )

    # Then: filler area does not produce the phrase area surface.
    assert result.cards[0].context_terms == ("surface",)
    assert all("area surface" not in prompt for prompt in prompts)


def test_render_equivalent_cards_are_deduped_but_evidence_rows_remain(
    tmp_path: Path,
) -> None:
    # Given: two rough candidates that render to identical refinement prompts.
    rough_root = tmp_path / "rough"
    records_path = rough_root / "lane-a" / "image-001-object-02" / "owlv2_sam2"
    records_path.mkdir(parents=True)
    _ = (records_path / "records.json").write_text(
        json.dumps(
            [
                {
                    "accepted": True,
                    "candidate_id": f"candidate-{index:03d}",
                    "image": "image-001",
                    "prompt": "white deposit on rim",
                }
                for index in range(2)
            ],
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    queries_path = tmp_path / "queries.jsonl"
    results_path = tmp_path / "prompt_rag_results.jsonl"
    write_jsonl(
        queries_path,
        (
            {
                "lane": "lane-a",
                "prompt_text": "white deposit on rim",
                "query_id": "lane-a:prompt-01",
            },
        ),
    )
    write_jsonl(
        results_path,
        (
            {
                "chunk_id": "chunk-001",
                "citation_id": "citation-001",
                "lane": "lane-a",
                "matched_terms": ["white", "deposit"],
                "prompt_text": "white deposit on rim",
                "query_id": "lane-a:prompt-01",
                "rank": 1,
                "score": 7.5,
                "snippet_text": "white deposit on a ceramic surface",
            },
        ),
    )

    # When: candidate sidecars are built.
    result = build_candidate_rag_sidecars(
        CandidateRagSidecarInputs(rough_root, queries_path, results_path)
    )

    # Then: accounting remains complete while duplicate prompt cards collapse.
    assert len(result.evidence_rows) == 2
    assert len(result.cards) == 1
