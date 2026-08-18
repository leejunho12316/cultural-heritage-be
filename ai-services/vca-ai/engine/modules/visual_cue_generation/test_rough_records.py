from __future__ import annotations

import json
from hashlib import sha256
from typing import TYPE_CHECKING

from modules.prompt_generating import static_seed_minimal_pack
from modules.rag.operations.candidate_sidecar_artifacts import read_rough_records
from modules.shared import DetectorLane, ImageId
from modules.visual_cue_generation.rough_records import rough_qwen_candidates

if TYPE_CHECKING:
    from pathlib import Path


def _asset(path: Path, contents: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(contents)
    return sha256(contents).hexdigest()


def test_rough_qwen_candidates_preserve_rag_candidate_ids(tmp_path: Path) -> None:
    # Given: a rough records file without explicit candidate IDs.
    lane_root = tmp_path / "owlv2_sam2" / "image-001-object-01" / "owlv2_sam2"
    mask_hash = _asset(
        lane_root / "masks" / "anomaly-0000.png",
        b"\x89PNG\r\n\x1a\nmask",
    )
    overlay_hash = _asset(
        lane_root / "overlays" / "anomaly-0000.jpg",
        b"\xff\xd8overlay",
    )
    _ = _asset(tmp_path / "source.jpg", b"\xff\xd8source")
    _ = (lane_root / "records.json").write_text(
        json.dumps(
            [
                {
                    "accepted": True,
                    "bbox_xyxy": [1.0, 2.0, 11.0, 12.0],
                    "view_origin_xyxy": [0.0, 0.0, 64.0, 64.0],
                    "view_image_path": str(tmp_path / "source.jpg"),
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

    # When: visual cue generation adapts rough records for Qwen.
    qwen_candidates = rough_qwen_candidates(tmp_path)
    rag_candidates = read_rough_records(tmp_path)

    # Then: the Qwen candidate identity matches RAG sidecar joins.
    assert len(qwen_candidates) == 1
    assert qwen_candidates[0].rough.candidate_id == rag_candidates[0].candidate_id
    assert qwen_candidates[0].candidate.candidate_id == rag_candidates[0].candidate_id
    assert qwen_candidates[0].candidate.image_id == ImageId("image-001")
    assert qwen_candidates[0].candidate.lane is DetectorLane.OWLV2_SAM2
    assert qwen_candidates[0].candidate.rough_mask.sha256 == mask_hash
    assert qwen_candidates[0].candidate.overlay.sha256 == overlay_hash
    assert qwen_candidates[0].candidate.prompt_provenance == (
        static_seed_minimal_pack.records[0].metadata
    )


def test_rough_qwen_candidates_exclude_rejected_records(tmp_path: Path) -> None:
    # Given: matching accepted and rejected rough records.
    lane_root = tmp_path / "owlv2_sam2" / "image-001-object-01" / "owlv2_sam2"
    mask_hash = _asset(lane_root / "masks/anomaly-0000.png", b"mask")
    overlay_hash = _asset(lane_root / "overlays/anomaly-0000.jpg", b"overlay")
    _ = _asset(tmp_path / "source.jpg", b"\xff\xd8source")
    _ = (lane_root / "records.json").write_text(
        json.dumps(
            [
                {
                    "accepted": accepted,
                    "bbox_xyxy": [1.0, 2.0, 11.0, 12.0],
                    "view_origin_xyxy": [0.0, 0.0, 64.0, 64.0],
                    "view_image_path": str(tmp_path / "source.jpg"),
                    "generation_lane": "owlv2",
                    "image": "image-001",
                    "mask_path": "masks/anomaly-0000.png",
                    "mask_semantics": "anomaly_region",
                    "overlay_path": "overlays/anomaly-0000.jpg",
                    "prompt": "surface crack",
                    "prompt_pack_id": "static-seed-minimal-v1",
                    "score": 0.8,
                }
                for accepted in (True, False)
            ],
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    # When: rough records are adapted for Qwen refinement.
    candidates = rough_qwen_candidates(tmp_path)

    # Then: only the accepted candidate can reach Qwen.
    assert len(candidates) == 1
    assert candidates[0].candidate.rough_mask.sha256 == mask_hash
    assert candidates[0].candidate.overlay.sha256 == overlay_hash
