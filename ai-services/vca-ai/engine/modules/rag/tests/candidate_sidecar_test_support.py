"""Shared builders for candidate sidecar tests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from modules.prompt_generating import (
    BoundaryRelation,
    ColorBucket,
    Morphology,
    SizeClass,
    TextureProxy,
    VisualCue,
)
from modules.rag.operations.candidate_sidecars import (
    CandidateRagSidecarInputs,
    build_candidate_rag_sidecars,
)
from modules.shared import CandidateId, QwenBridgeResult, QwenBridgeStatus

if TYPE_CHECKING:
    from pathlib import Path


JsonlValue = str | int | float | list[str]
JsonlRow = dict[str, JsonlValue]


def write_jsonl(path: Path, rows: tuple[JsonlRow, ...]) -> None:
    """Write deterministic JSONL fixture rows."""
    _ = path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


def make_candidate_sidecar_inputs(tmp_path: Path) -> CandidateRagSidecarInputs:
    """Create the canonical candidate sidecar fixture inputs."""
    rough_root = _rough_root(tmp_path)
    queries_path = tmp_path / "queries.jsonl"
    results_path = tmp_path / "prompt_rag_results.jsonl"
    _queries(queries_path)
    _results(results_path)
    return CandidateRagSidecarInputs(
        rough_records_root=rough_root,
        queries_path=queries_path,
        prompt_rag_results_path=results_path,
    )


def first_candidate_id(inputs: CandidateRagSidecarInputs) -> str:
    """Return the first candidate id from built sidecar evidence."""
    return build_candidate_rag_sidecars(inputs).evidence_rows[0].rag_parent_candidate_id


def successful_qwen_result(candidate_id: CandidateId) -> QwenBridgeResult:
    """Create a successful Qwen bridge result fixture."""
    return QwenBridgeResult(
        candidate_id=candidate_id,
        status=QwenBridgeStatus.SUCCESS,
        selected_terms=("white powder",),
        extracted_descriptors=("powdery",),
        confidence=0.91,
        reason="explicit bridge result",
        qwen_observation_id="qwen-001",
        input_view_hashes=("viewhash-001",),
    )


def visual_cue() -> VisualCue:
    """Create an explicit visual cue fixture."""
    return VisualCue(
        color_bucket=ColorBucket.WHITE,
        morphology=Morphology.CRUST,
        texture_proxy=TextureProxy.POWDERY,
        size_class=SizeClass.LOCAL,
        boundary_relation=BoundaryRelation.INTERIOR,
        confidence=0.88,
        reasons=("white", "crust", "powdery"),
    )


def _rough_root(tmp_path: Path) -> Path:
    root = tmp_path / "rough"
    records_path = root / "lane-a" / "image-001-object-02" / "owlv2_sam2"
    records_path.mkdir(parents=True)
    _ = (records_path / "records.json").write_text(
        json.dumps(
            [
                {
                    "accepted": True,
                    "bbox_xyxy": [float(index), 2.0, 3.0, 4.0],
                    "image": "image-001",
                    "mask_path": f"masks/anomaly-{index:04d}.png",
                    "overlay_path": f"overlays/anomaly-{index:04d}.jpg",
                    "prompt": prompt,
                    "prompt_pack_id": "static-seed-minimal-v1",
                    "score": score,
                }
                for index, (prompt, score) in enumerate(
                    (("white deposit on rim", 0.7), ("dark stain on rim", 0.6))
                )
            ],
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return root


def _queries(path: Path) -> None:
    write_jsonl(
        path,
        (
            {
                "lane": "lane-b",
                "prompt_text": "white deposit on rim",
                "query_id": "lane-b:prompt-01",
                "query_terms": ["wrong", "lane"],
            },
            {
                "lane": "lane-a",
                "prompt_text": "white deposit on rim",
                "query_id": "lane-a:prompt-01",
                "query_terms": ["white", "deposit"],
            },
            {
                "lane": "lane-a",
                "prompt_text": "dark stain on rim",
                "query_id": "lane-a:prompt-02",
                "query_terms": ["dark", "stain"],
            },
            # successful_qwen_result()가 만드는 시그니처
            # (qwen_query_signature: selected_terms=("white powder",),
            # extracted_descriptors=("powdery",)) 전용 행. 빈 시그니처 행
            # (lane-a:prompt-01)은 건드리지 않는다 - no-qwen 테스트들이 그걸 쓴다.
            {
                "lane": "lane-a",
                "prompt_text": "white deposit on rim",
                "query_id": "lane-a:prompt-01-qwen",
                "qwen_signature": ["powdery", "white powder"],
                "query_terms": ["white", "deposit", "powdery"],
            },
        ),
    )


def _results(path: Path) -> None:
    write_jsonl(
        path,
        (
            {
                "chunk_id": "chunk-001",
                "citation_id": "citation-001",
                "lane": "lane-a",
                "matched_terms": ["white", "deposit"],
                "page_text": "raw OCR text must not become a prompt",
                "prompt_text": "white deposit on rim",
                "query_id": "lane-a:prompt-01",
                "rank": 1,
                "score": 7.5,
                "snippet_text": "white powder accretion on a ceramic rim",
            },
            {
                "chunk_id": "chunk-002",
                "citation_id": "citation-002",
                "lane": "lane-a",
                "matched_terms": ["dark", "stain"],
                "page_text": "raw page text also stays out",
                "prompt_text": "dark stain on rim",
                "query_id": "lane-a:prompt-02",
                "rank": 1,
                "score": 3.0,
                "snippet_text": "dark staining at the rim edge",
            },
            {
                "chunk_id": "chunk-003",
                "citation_id": "citation-003",
                "lane": "lane-b",
                "matched_terms": ["wrong"],
                "page_text": "wrong lane page text",
                "prompt_text": "white deposit on rim",
                "query_id": "lane-b:prompt-01",
                "rank": 1,
                "score": 99.0,
                "snippet_text": "wrong lane must not join",
            },
            {
                "chunk_id": "chunk-004",
                "citation_id": "citation-004",
                "lane": "lane-a",
                "matched_terms": ["white", "deposit"],
                "page_text": "raw OCR text must not become a prompt",
                "prompt_text": "white deposit on rim",
                "query_id": "lane-a:prompt-01-qwen",
                "rank": 1,
                "score": 8.5,
                "snippet_text": "powdery white accretion documented on a similar rim",
            },
        ),
    )
