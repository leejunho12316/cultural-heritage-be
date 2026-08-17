from __future__ import annotations

import json
from hashlib import sha256
from typing import TYPE_CHECKING, Literal

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

type ManifestFailureCase = Literal["hash_mismatch", "missing_source", "unsafe_source"]


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_rough_record(root: Path) -> None:
    lane_root = root / "owlv2_sam2" / "image-001-object-01" / "owlv2_sam2"
    lane_root.mkdir(parents=True)
    _ = (lane_root / "masks" / "anomaly-0000.png").parent.mkdir(parents=True)
    _ = (lane_root / "overlays" / "anomaly-0000.jpg").parent.mkdir(parents=True)
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


def _write_input_manifest(path: Path, project_root: Path) -> None:
    source_path = project_root / "source.jpg"
    Image.new("RGB", (64, 64), (255, 255, 255)).save(source_path, format="JPEG")
    source_bytes = source_path.read_bytes()
    _write_json(
        path,
        {
            "images": [
                {
                    "file_sha256": sha256(source_bytes).hexdigest(),
                    "image_id": "image-001",
                    "mime_type": "image/jpeg",
                    "run_root_asset_path": str(source_path),
                }
            ]
        },
    )


def _write_invalid_manifest(
    tmp_path: Path, manifest_path: Path, failure_case: ManifestFailureCase
) -> tuple[Path, str]:
    asset_root = tmp_path
    source_path = tmp_path / "source.jpg"
    expected_hash = "0" * 64
    match failure_case:
        case "hash_mismatch":
            Image.new("RGB", (64, 64), (255, 255, 255)).save(source_path, format="JPEG")
            match_text = "file_sha256"
        case "missing_source":
            source_path = tmp_path / "missing.jpg"
            match_text = "run_root_asset_path"
        case "unsafe_source":
            asset_root = tmp_path / "assets"
            asset_root.mkdir()
            source_path = tmp_path / "outside.jpg"
            Image.new("RGB", (64, 64), (255, 255, 255)).save(source_path, format="JPEG")
            expected_hash = sha256(source_path.read_bytes()).hexdigest()
            match_text = "run_root_asset_path"
    _write_json(
        manifest_path,
        {
            "images": [
                {
                    "file_sha256": expected_hash,
                    "image_id": "image-001",
                    "mime_type": "image/jpeg",
                    "run_root_asset_path": str(source_path),
                }
            ]
        },
    )
    return asset_root, match_text


def test_generate_qwen_bridge_results_writes_rag_run_artifact_from_rough_records(
    tmp_path: Path,
) -> None:
    # Given: one rough-mask record and a preprocessing image manifest.
    rough_root = tmp_path / "output" / "rough_masking" / "run-001"
    rag_run = tmp_path / "output" / "rag" / "run-001"
    manifest_path = tmp_path / "output" / "preprocessing" / "manifest.json"
    _write_rough_record(rough_root)
    _write_input_manifest(manifest_path, tmp_path)

    # When: visual_cue_generation runs Qwen evidence for rough candidates.
    result = generate_qwen_bridge_results(
        QwenBridgeGenerationInputs(
            rough_root=rough_root,
            rag_run_dir=rag_run,
            asset_root=tmp_path,
            input_manifest_path=manifest_path,
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


def test_generate_qwen_bridge_results_preserves_failed_rows_for_missing_source(
    tmp_path: Path,
) -> None:
    # Given: rough candidates whose source image is absent from the manifest.
    rough_root = tmp_path / "rough"
    manifest_path = tmp_path / "manifest.json"
    _write_rough_record(rough_root)
    _write_json(manifest_path, {"images": []})

    # When: generation cannot build a source-backed Qwen request.
    result = generate_qwen_bridge_results(
        QwenBridgeGenerationInputs(
            rough_root=rough_root,
            rag_run_dir=tmp_path / "rag",
            asset_root=tmp_path,
            input_manifest_path=manifest_path,
            device="cpu",
        ),
        IndependentRenderer(tmp_path),
        IndependentBackend(valid_observation_json()),
    )

    # Then: the candidate remains represented as a failed bridge row.
    rows = tuple(read_qwen_bridge_results(result.artifact_path).values())
    assert result.processed_candidates == 1
    assert result.successful_results == 0
    assert result.failed_results == 1
    assert rows[0].status is QwenBridgeStatus.FAILED
    assert rows[0].failure_code == "source_asset_missing"


def test_generate_qwen_bridge_results_preserves_failed_rows_for_decode_errors(
    tmp_path: Path,
) -> None:
    # Given: a valid record whose source asset is not a decodable image.
    rough_root = tmp_path / "rough"
    manifest_path = tmp_path / "manifest.json"
    _write_rough_record(rough_root)
    source_path = tmp_path / "corrupt-source.jpg"
    _ = source_path.write_bytes(b"not-an-image")
    _write_json(
        manifest_path,
        {
            "images": [
                {
                    "file_sha256": sha256(source_path.read_bytes()).hexdigest(),
                    "image_id": "image-001",
                    "mime_type": "image/jpeg",
                    "run_root_asset_path": str(source_path),
                }
            ]
        },
    )

    # When: candidate image dimensions are decoded.
    result = generate_qwen_bridge_results(
        QwenBridgeGenerationInputs(
            rough_root=rough_root,
            rag_run_dir=tmp_path / "rag",
            asset_root=tmp_path,
            input_manifest_path=manifest_path,
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


@pytest.mark.parametrize(
    "failure_case", ["hash_mismatch", "missing_source", "unsafe_source"]
)
def test_generate_qwen_bridge_results_rejects_invalid_manifest_without_artifact(
    tmp_path: Path, failure_case: ManifestFailureCase
) -> None:
    # Given: a rough candidate and a batch-fatal source manifest integrity failure.
    rough_root = tmp_path / "rough"
    rag_run = tmp_path / "rag"
    manifest_path = tmp_path / "manifest.json"
    _write_rough_record(rough_root)
    asset_root, match_text = _write_invalid_manifest(
        tmp_path, manifest_path, failure_case
    )

    # When/Then: manifest integrity failure aborts before any bridge artifact write.
    with pytest.raises(ContractValidationError, match=match_text):
        _ = generate_qwen_bridge_results(
            QwenBridgeGenerationInputs(
                rough_root=rough_root,
                rag_run_dir=rag_run,
                asset_root=asset_root,
                input_manifest_path=manifest_path,
                device="cpu",
            ),
            IndependentRenderer(asset_root),
            IndependentBackend(valid_observation_json()),
        )
    assert not (rag_run / "qwen_bridge_results").exists()
