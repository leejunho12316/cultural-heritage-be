from __future__ import annotations

import json
from typing import TYPE_CHECKING

from modules.rag.operations.candidate_sidecars import (
    CandidateRagSidecarInputs,
    build_candidate_rag_sidecars,
)

from .candidate_sidecar_test_support import write_jsonl

if TYPE_CHECKING:
    from pathlib import Path


def test_unrecognized_prompt_family_becomes_unknown_visual_anomaly(
    tmp_path: Path,
) -> None:
    # Given: a prompt without a specific concept-family keyword.
    rough_root = tmp_path / "rough"
    records_path = rough_root / "lane-a" / "image-001-object-02" / "owlv2_sam2"
    records_path.mkdir(parents=True)
    _ = (records_path / "records.json").write_text(
        json.dumps(
            [
                {
                    "accepted": True,
                    "bbox_xyxy": [1.0, 2.0, 3.0, 4.0],
                    "image": "image-001",
                    "mask_path": "masks/anomaly-0000.png",
                    "overlay_path": "overlays/anomaly-0000.jpg",
                    "prompt": "unusual surface mark",
                    "prompt_pack_id": "static-seed-minimal-v1",
                    "score": 0.7,
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
                "prompt_text": "unusual surface mark",
                "query_id": "lane-a:prompt-01",
                "query_terms": ["surface", "mark"],
            },
        ),
    )
    write_jsonl(
        results_path,
        (
            {
                "chunk_id": "chunk-004",
                "citation_id": "citation-004",
                "lane": "lane-a",
                "matched_terms": ["surface", "spot"],
                "page_text": "raw page text stays out",
                "prompt_text": "unusual surface mark",
                "query_id": "lane-a:prompt-01",
                "rank": 1,
                "score": 2.0,
                "snippet_text": "localized visual mark on a glass surface",
            },
        ),
    )

    # When: sidecars are built.
    result = build_candidate_rag_sidecars(
        CandidateRagSidecarInputs(rough_root, queries_path, results_path)
    )

    # Then: the candidate is not silently dropped for a missing specific family.
    assert len(result.cards) == 1
    card = result.cards[0]
    assert card.concept_family is not None
    assert card.concept_family.value == "unknown_visual_anomaly"
    assert card.material_terms == ()


def test_snippet_only_descriptor_and_material_terms_do_not_emit_card(
    tmp_path: Path,
) -> None:
    # Given: retrieval rows where all actionable words appear only in snippet text.
    rough_root = tmp_path / "rough"
    records_path = rough_root / "lane-a" / "image-001-object-02" / "owlv2_sam2"
    records_path.mkdir(parents=True)
    _ = (records_path / "records.json").write_text(
        json.dumps(
            [
                {
                    "accepted": True,
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
                "matched_terms": ["deposit"],
                "prompt_text": "white deposit on rim",
                "query_id": "lane-a:prompt-01",
                "rank": 1,
                "score": 7.5,
                "snippet_text": "white powder on ceramic surface",
            },
        ),
    )

    # When: sidecars are built without explicit visual cues.
    result = build_candidate_rag_sidecars(
        CandidateRagSidecarInputs(rough_root, queries_path, results_path)
    )

    # Then: raw snippet text is not mined into a prompt-ready concept card.
    assert len(result.evidence_rows) == 1
    assert result.cards == ()


def test_ocr_table_top_result_is_skipped_before_card_creation(
    tmp_path: Path,
) -> None:
    # Given: a garbage top result followed by usable visual retrieval evidence.
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
                    "prompt": "white crack on rim",
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
                "prompt_text": "white crack on rim",
                "query_id": "lane-a:prompt-01",
            },
        ),
    )
    write_jsonl(
        results_path,
        (
            {
                "chunk_id": "chunk-garbage",
                "citation_id": "citation-garbage",
                "lane": "lane-a",
                "matched_terms": ["smooth", "crack", "area"],
                "prompt_text": "white crack on rim",
                "query_id": "lane-a:prompt-01",
                "rank": 1,
                "score": 30.0,
                "snippet_text": "956 Unchanging area Unchanging area 0-300 0-300 0",
            },
            {
                "chunk_id": "chunk-usable",
                "citation_id": "citation-usable",
                "lane": "lane-a",
                "matched_terms": ["white", "crack"],
                "prompt_text": "white crack on rim",
                "query_id": "lane-a:prompt-01",
                "rank": 2,
                "score": 12.0,
                "snippet_text": "white line visible on a ceramic surface",
            },
        ),
    )

    # When: sidecars select prompt-renderable evidence.
    result = build_candidate_rag_sidecars(
        CandidateRagSidecarInputs(rough_root, queries_path, results_path)
    )

    # Then: the table fragment is accounted but cannot become the concept card.
    assert len(result.evidence_rows) == 1
    assert len(result.cards) == 1
    card = result.cards[0]
    assert card.source_citation_ids == ("citation-usable",)
    assert "Unchanging area" not in card.raw_retrieved_sentence
    assert "smooth" not in card.descriptor_terms
