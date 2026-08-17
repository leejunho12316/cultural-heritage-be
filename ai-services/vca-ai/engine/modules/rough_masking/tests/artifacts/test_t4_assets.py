from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from modules.rough_masking import execute_adapter
from modules.rough_masking.tests.candidates.test_t4 import (
    invoked_runner,
    make_record,
    make_request,
    write_records,
)
from modules.shared import DetectorLane

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class AssetFixture:
    path: str
    contents: bytes


@dataclass(frozen=True, slots=True)
class AssetCase:
    assets: tuple[AssetFixture, ...]
    record_field: str
    record_value: str
    diagnostic: str


@pytest.mark.parametrize(
    "case",
    [
        AssetCase(
            (AssetFixture("overlay.jpg", b"overlay"),),
            "mask_path",
            "missing.png",
            "mask_file_missing",
        ),
        AssetCase(
            (AssetFixture("mask.png", b"mask"),),
            "overlay_path",
            "missing.jpg",
            "overlay_file_missing",
        ),
        AssetCase(
            (
                AssetFixture("mask.txt", b"not a mask"),
                AssetFixture("overlay.jpg", b"overlay"),
            ),
            "mask_path",
            "mask.txt",
            "mask_media_type_invalid",
        ),
        AssetCase(
            (
                AssetFixture("mask.png", b"mask"),
                AssetFixture("overlay.png", b"not an overlay"),
            ),
            "overlay_path",
            "overlay.png",
            "overlay_media_type_invalid",
        ),
    ],
)
def test_adapter_rejects_missing_or_unsupported_assets(
    tmp_path: Path, case: AssetCase
) -> None:
    # Given: a valid record changed to reference a missing or unsupported asset.
    request = make_request(tmp_path, DetectorLane.OWLV2_SAM2)
    request.lane_output_dir.mkdir()
    for asset in case.assets:
        _ = (request.lane_output_dir / asset.path).write_bytes(asset.contents)
    record = make_record(request)
    record[case.record_field] = case.record_value
    write_records(request, [record])

    # When: the runner record is normalized.
    receipt = execute_adapter(request, invoked_runner)

    # Then: its invalid asset cannot become a candidate.
    assert receipt.candidates == ()
    assert receipt.diagnostics == (case.diagnostic,)


@pytest.mark.parametrize(
    ("field", "value", "diagnostic"),
    [
        ("prompt_pack_id", "wrong-pack", "prompt_pack_id_mismatch"),
        ("generation_lane", "florence2", "generation_lane_mismatch"),
        ("prompt", "unlocked prompt", "prompt_not_locked_seed"),
    ],
)
def test_adapter_rejects_prompt_provenance_mismatches(
    tmp_path: Path, field: str, value: str, diagnostic: str
) -> None:
    # Given: a contained candidate record with falsified prompt provenance.
    request = make_request(tmp_path, DetectorLane.OWLV2_SAM2)
    request.lane_output_dir.mkdir()
    _ = (request.lane_output_dir / "mask.png").write_bytes(b"mask")
    _ = (request.lane_output_dir / "overlay.jpg").write_bytes(b"overlay")
    record = make_record(request)
    record[field] = value
    write_records(request, [record])

    # When: normalization reads the record.
    receipt = execute_adapter(request, invoked_runner)

    # Then: provenance mismatch cannot become a candidate.
    assert receipt.candidates == ()
    assert receipt.diagnostics == (diagnostic,)


@pytest.mark.parametrize(
    ("asset_path", "initial", "changed"),
    [
        ("mask.png", b"mask-v1", b"mask-v2"),
        ("overlay.jpg", b"overlay-v1", b"overlay-v2"),
    ],
)
def test_candidate_identity_includes_asset_content(
    tmp_path: Path, asset_path: str, initial: bytes, changed: bytes
) -> None:
    # Given: a valid record with stable paths and initial mask/overlay contents.
    request = make_request(tmp_path, DetectorLane.OWLV2_SAM2)
    request.lane_output_dir.mkdir()
    _ = (request.lane_output_dir / "mask.png").write_bytes(b"mask-v1")
    _ = (request.lane_output_dir / "overlay.jpg").write_bytes(b"overlay-v1")
    _ = (request.lane_output_dir / asset_path).write_bytes(initial)
    write_records(request, [make_record(request)])
    initial_receipt = execute_adapter(request, invoked_runner)

    # When: the referenced asset content changes while the record stays stable.
    _ = (request.lane_output_dir / asset_path).write_bytes(changed)
    changed_receipt = execute_adapter(request, invoked_runner)

    # Then: the content-addressed candidate ID changes.
    assert (
        initial_receipt.candidates[0].candidate_id
        != changed_receipt.candidates[0].candidate_id
    )


def test_candidate_identity_excludes_quality_metadata(tmp_path: Path) -> None:
    # Given: a valid record with stable assets and initial quality metadata.
    request = make_request(tmp_path, DetectorLane.OWLV2_SAM2)
    request.lane_output_dir.mkdir()
    _ = (request.lane_output_dir / "mask.png").write_bytes(b"mask")
    _ = (request.lane_output_dir / "overlay.jpg").write_bytes(b"overlay")
    record = make_record(request)
    record["quality_filter_version"] = "rough-mask-quality-v1"
    record["quality_score"] = 0.2
    write_records(request, [record])
    initial_receipt = execute_adapter(request, invoked_runner)

    # When: only quality metadata changes.
    record["quality_score"] = 0.9
    record["boundary_pixel_ratio"] = 0.5
    record["perimeter_coverage_ratio"] = 0.7
    write_records(request, [record])
    changed_receipt = execute_adapter(request, invoked_runner)

    # Then: candidate identity remains tied to semantic content and assets.
    assert (
        initial_receipt.candidates[0].candidate_id
        == changed_receipt.candidates[0].candidate_id
    )
