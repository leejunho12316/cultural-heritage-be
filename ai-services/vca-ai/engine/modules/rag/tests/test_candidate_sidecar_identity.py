from __future__ import annotations

import json
from typing import TYPE_CHECKING

from modules.rag.operations.candidate_sidecar_artifacts import read_rough_records

if TYPE_CHECKING:
    from pathlib import Path


def _write_rough_records(
    root: Path, rows: tuple[dict[str, object], ...]
) -> Path:
    records_path = root / "lane-a" / "image-001-object-02" / "owlv2_sam2"
    records_path.mkdir(parents=True)
    _ = (records_path / "records.json").write_text(
        json.dumps(rows, sort_keys=True),
        encoding="utf-8",
    )
    return root


def _accepted_record(prompt: str, asset_index: int) -> dict[str, object]:
    return {
        "accepted": True,
        "bbox_xyxy": [float(asset_index), 2.0, 3.0, 4.0],
        "image": "image-001",
        "mask_path": f"masks/anomaly-{asset_index:04d}.png",
        "overlay_path": f"overlays/anomaly-{asset_index:04d}.jpg",
        "prompt": prompt,
        "prompt_pack_id": "static-seed-minimal-v1",
        "score": 0.7,
    }


def _rejected_record() -> dict[str, object]:
    return {
        "accepted": False,
        "bbox_xyxy": [0.0, 2.0, 3.0, 4.0],
        "image": "image-001",
        "prompt": "rejected halo",
        "reject_reason": "boundary_halo",
        "score": 0.1,
    }


def test_rejected_rough_records_do_not_shift_fallback_candidate_identity(
    tmp_path: Path,
) -> None:
    # Given: the same accepted candidates with and without rejected telemetry rows.
    plain = _write_rough_records(
        tmp_path / "plain",
        (
            _accepted_record("white deposit on rim", 0),
            _accepted_record("dark stain on rim", 1),
        ),
    )
    interleaved = _write_rough_records(
        tmp_path / "interleaved",
        (
            _rejected_record(),
            _accepted_record("white deposit on rim", 0),
            _rejected_record(),
            _accepted_record("dark stain on rim", 1),
        ),
    )

    # When: RAG reads rough records through fallback candidate IDs.
    plain_candidates = read_rough_records(plain)
    interleaved_candidates = read_rough_records(interleaved)

    # Then: rejected rows do not change accepted fallback identities.
    interleaved_ids = tuple(
        candidate.candidate_id for candidate in interleaved_candidates
    )
    plain_ids = tuple(candidate.candidate_id for candidate in plain_candidates)
    assert interleaved_ids == plain_ids


def test_rejected_rough_records_preserve_raw_record_index(
    tmp_path: Path,
) -> None:
    # Given: rejected rows before and between accepted records.
    root = _write_rough_records(
        tmp_path / "rough",
        (
            _rejected_record(),
            _accepted_record("white deposit on rim", 0),
            _rejected_record(),
            _accepted_record("dark stain on rim", 1),
        ),
    )

    # When: accepted rough records are read.
    candidates = read_rough_records(root)

    # Then: raw indexes still point back to the source records.json entries.
    assert tuple(candidate.rough_record_index for candidate in candidates) == (1, 3)
    assert str(candidates[0].candidate_id).endswith("record-0000")
    assert str(candidates[1].candidate_id).endswith("record-0001")
