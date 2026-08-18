from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from modules.mask_refining import PillowQwenViewRenderer
from modules.mask_refining.tests.test_support import (
    IndependentBackend,
    IndependentRenderer,
    valid_observation_json,
)
from modules.rag.qwen import read_qwen_bridge_results
from modules.shared import CandidateId, ContractValidationError, QwenBridgeStatus
from modules.visual_cue_generation import (
    QwenBridgeGenerationInputs,
    generate_qwen_bridge_results,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_rough_record(root: Path, view_image_path: Path) -> None:
    lane_root = root / "owlv2_sam2" / "image-001-object-01" / "owlv2_sam2"
    lane_root.mkdir(parents=True)
    (lane_root / "masks").mkdir(parents=True)
    (lane_root / "overlays").mkdir(parents=True)
    Image.new("L", (64, 64), 255).save(
        lane_root / "masks" / "anomaly-0000.png",
        format="PNG",
    )
    _ = (lane_root / "overlays" / "anomaly-0000.jpg").write_bytes(b"\xff\xd8overlay")
    _ = (lane_root / "records.json").write_text(
        json.dumps(
            [
                {
                    "accepted": True,
                    "bbox_xyxy": [10.0, 12.0, 30.0, 32.0],
                    "view_origin_xyxy": [0.0, 0.0, 64.0, 64.0],
                    "view_image_path": str(view_image_path),
                    "generation_lane": "owlv2",
                    "image": "image-001",
                    "mask_path": "masks/anomaly-0000.png",
                    "mask_semantics": "anomaly_region",
                    "overlay_path": "overlays/anomaly-0000.jpg",
                    "prompt": "surface crack",
                    "prompt_pack_id": "static-seed-minimal-v1",
                    "score": 0.8,
                }
            ],
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def test_generate_qwen_bridge_results_writes_rag_run_artifact_from_rough_records(
    tmp_path: Path,
) -> None:
    # Given: one rough-mask record whose view_image_path is a real, decodable photo.
    rough_root = tmp_path / "output" / "rough_masking" / "run-001"
    rag_run = tmp_path / "output" / "rag" / "run-001"
    view_image_path = tmp_path / "source.jpg"
    Image.new("RGB", (64, 64), (255, 255, 255)).save(view_image_path, format="JPEG")
    _write_rough_record(rough_root, view_image_path)

    # When: visual_cue_generation runs Qwen evidence for rough candidates.
    result = generate_qwen_bridge_results(
        QwenBridgeGenerationInputs(
            rough_root=rough_root,
            rag_run_dir=rag_run,
            asset_root=tmp_path,
            device="cpu",
        ),
        PillowQwenViewRenderer(tmp_path),
        IndependentBackend(valid_observation_json()),
    )

    # Then: it writes the run-local bridge artifact keyed like RAG rough records.
    rows = read_qwen_bridge_results(result.artifact_path)
    candidate_id = CandidateId(
        "rough:owlv2_sam2:image-001:image-001-object-01_owlv2_sam2:record-0000"
    )
    assert result.processed_candidates == 1
    assert result.successful_results == 1
    assert result.failed_results == 0
    assert result.artifact_path == rag_run / "qwen_bridge_results"
    assert result.artifact_path.is_dir()
    candidate_file = (
        result.artifact_path
        / "owlv2_sam2/image-001-object-01/owlv2_sam2"
        / "record-0000"
        / "qwen_bridge_result.jsonl"
    )
    candidate_lines = candidate_file.read_text(encoding="utf-8").splitlines()
    assert len(candidate_lines) == 1
    assert json.loads(candidate_lines[0])["candidate_id"] == str(candidate_id)
    assert rows[candidate_id].status is QwenBridgeStatus.SUCCESS
    assert rows[candidate_id].selected_terms == ("brown region",)
    assert len(rows[candidate_id].input_view_hashes) == 2
    assert not (rag_run / "qwen_bridge_results.jsonl").exists()


def test_generate_qwen_bridge_results_raises_when_view_image_missing(
    tmp_path: Path,
) -> None:
    # Given: a rough candidate whose own view_image_path does not exist on disk.
    # Unlike the old full-photo lookup, this is now checked at the same
    # candidate-reconstruction step as mask_path/overlay_path (rough_records.py),
    # so a missing view image is a hard batch failure, not a soft per-row one.
    rough_root = tmp_path / "rough"
    _write_rough_record(rough_root, tmp_path / "missing.jpg")

    # When/Then: generation cannot even reconstruct the candidate.
    with pytest.raises(ContractValidationError, match="asset missing"):
        _ = generate_qwen_bridge_results(
            QwenBridgeGenerationInputs(
                rough_root=rough_root,
                rag_run_dir=tmp_path / "rag",
                asset_root=tmp_path,
                device="cpu",
            ),
            IndependentRenderer(tmp_path),
            IndependentBackend(valid_observation_json()),
        )


def test_generate_qwen_bridge_results_raises_when_view_image_escapes_asset_root(
    tmp_path: Path,
) -> None:
    # Given: a rough candidate whose view_image_path points outside asset_root.
    rough_root = tmp_path / "assets" / "rough"
    outside_image = tmp_path / "outside.jpg"
    Image.new("RGB", (64, 64), (255, 255, 255)).save(outside_image, format="JPEG")
    _write_rough_record(rough_root, outside_image)

    # When/Then: the path-containment check rejects it before any Qwen call.
    with pytest.raises(ContractValidationError, match="asset root"):
        _ = generate_qwen_bridge_results(
            QwenBridgeGenerationInputs(
                rough_root=rough_root,
                rag_run_dir=tmp_path / "rag",
                asset_root=tmp_path / "assets",
                device="cpu",
            ),
            IndependentRenderer(tmp_path / "assets"),
            IndependentBackend(valid_observation_json()),
        )


def test_generate_qwen_bridge_results_preserves_failed_rows_for_decode_errors(
    tmp_path: Path,
) -> None:
    # Given: a valid record whose view_image_path is not a decodable image.
    rough_root = tmp_path / "rough"
    view_image_path = tmp_path / "corrupt-source.jpg"
    _ = view_image_path.write_bytes(b"not-an-image")
    _write_rough_record(rough_root, view_image_path)

    # When: candidate image dimensions are decoded.
    result = generate_qwen_bridge_results(
        QwenBridgeGenerationInputs(
            rough_root=rough_root,
            rag_run_dir=tmp_path / "rag",
            asset_root=tmp_path,
            device="cpu",
        ),
        IndependentRenderer(tmp_path),
        IndependentBackend(valid_observation_json()),
    )

    # Then: the bad candidate is recorded as failed without aborting the batch.
    rows = tuple(read_qwen_bridge_results(result.artifact_path).values())
    assert result.processed_candidates == 1
    assert result.failed_results == 1
    assert rows[0].failure_code == "image_decode_failed"
