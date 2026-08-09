from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from modules.rough_masking import execute_adapter
from modules.rough_masking.contracts import (
    ImageDimensions,
    SeedRequestPaths,
    build_seed_request,
)
from modules.rough_masking.tests.candidates.test_t4 import (
    Record,
    invoked_runner,
    make_record,
    make_request,
    make_view,
    static_seed_minimal_pack,
    write_records,
)
from modules.shared import DetectorLane

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("record", "diagnostic"),
    [
        (
            {"accepted": False, "reject_reason": "low_score"},
            "record_rejected:low_score",
        ),
        ({"accepted": True}, "score_missing"),
        ({"accepted": True, "score": "bad"}, "score_invalid"),
        (
            {"accepted": True, "score": 0.2, "image": "image-001"},
            "bbox_missing",
        ),
        (
            {
                "accepted": True,
                "score": 0.2,
                "image": "image-001",
                "bbox_xyxy": [1, 1, float("nan"), 2],
            },
            "bbox_not_finite",
        ),
        (
            {
                "accepted": True,
                "score": 0.2,
                "image": "image-001",
                "bbox_xyxy": [2, 2, 1, 3],
            },
            "bbox_invalid_order",
        ),
        (
            {
                "accepted": True,
                "score": 0.2,
                "image": "image-001",
                "bbox_xyxy": [1, 2, 101, 30],
            },
            "bbox_outside_image",
        ),
        (
            {"accepted": True, "score": 0.2, "bbox_xyxy": [1, 2, 3, 4]},
            "image_id_missing",
        ),
    ],
)
def test_adapter_diagnoses_invalid_record_fields(
    tmp_path: Path, record: Record, diagnostic: str
) -> None:
    # Given: an invalid runner record in an invoked lane.
    request = make_request(tmp_path, DetectorLane.OWLV2_SAM2)
    request.lane_output_dir.mkdir()
    write_records(request, [record])

    # When: normalization receives the runner output.
    receipt = execute_adapter(request, invoked_runner)

    # Then: invalid data becomes diagnostics, never candidates.
    assert receipt.candidates == ()
    assert diagnostic in receipt.diagnostics


def test_adapter_accepts_lane_output_dir_nested_under_a_per_view_segment(
    tmp_path: Path,
) -> None:
    # Given: a lane_output_dir shaped like rough_masking's real per-object-view
    # layout - <lane>/<object_id>/<lane>/<view_segment> - where each tile/object
    # view gets its own leaf directory so their masks/overlays don't collide,
    # rather than the flat "<root>/<lane>" layout the other fixtures use.
    lane = DetectorLane.OWLV2_SAM2
    nested_dir = (
        tmp_path / lane.value / "image-001-object-01" / lane.value / "tile-001"
    )
    nested_dir.mkdir(parents=True)
    request = build_seed_request(
        lane=lane,
        view=make_view(),
        image_dimensions=ImageDimensions(100, 80),
        paths=SeedRequestPaths(
            lane_output_dir=nested_dir,
            records_json=nested_dir / "records.json",
        ),
        prompt_pack=static_seed_minimal_pack,
    )
    write_records(request, [make_record(request)])
    _ = (nested_dir / "mask.png").write_bytes(b"mask")
    _ = (nested_dir / "overlay.jpg").write_bytes(b"overlay")

    # When: the adapter normalizes this nested-directory execution.
    receipt = execute_adapter(request, invoked_runner)

    # Then: the nested per-view directory is accepted, not diagnosed as
    # lane_output_dir_invalid, as long as the lane's own name still appears
    # somewhere in the directory's ancestry.
    assert receipt.diagnostics == ()
    assert len(receipt.candidates) == 1


def test_adapter_diagnoses_malformed_json(tmp_path: Path) -> None:
    # Given: a lane records file containing malformed JSON.
    request = make_request(tmp_path, DetectorLane.OWLV2_SAM2)
    request.lane_output_dir.mkdir()
    _ = request.records_json.write_text("[")

    # When: the invoked adapter reads the records file.
    receipt = execute_adapter(request, invoked_runner)

    # Then: unreadable records JSON is a lane failure, not a real execution.
    assert receipt.status.value == "failed"
    assert receipt.candidates == ()
    assert receipt.diagnostics == ("records_json_invalid",)


@pytest.mark.parametrize(
    ("contents", "diagnostic"),
    [
        ('{"accepted": true}', "records_json_invalid"),
        ('["not-a-record"]', "records_json_invalid"),
        ('[{"unsupported": {"nested": true}}]', "records_json_invalid"),
    ],
)
def test_adapter_diagnoses_valid_json_with_unsupported_record_shapes(
    tmp_path: Path, contents: str, diagnostic: str
) -> None:
    # Given: valid JSON that does not satisfy the runner-record boundary.
    request = make_request(tmp_path, DetectorLane.OWLV2_SAM2)
    request.lane_output_dir.mkdir()
    _ = request.records_json.write_text(contents)

    # When: the adapter normalizes the invoked lane output.
    receipt = execute_adapter(request, invoked_runner)

    # Then: unsupported JSON shapes become diagnostics without candidates.
    assert receipt.candidates == ()
    assert receipt.diagnostics == (diagnostic,)


def test_adapter_accepts_zero_candidates_from_a_real_execution(tmp_path: Path) -> None:
    # Given: a real execution whose only record was rejected by the runner.
    request = make_request(tmp_path, DetectorLane.FLORENCE2_SAM2)
    request.lane_output_dir.mkdir()
    write_records(request, [{"accepted": False, "reject_reason": "low_score"}])

    # When: the adapter normalizes that execution.
    receipt = execute_adapter(request, invoked_runner)

    # Then: zero accepted candidates is not a lane failure.
    assert receipt.status.value == "real_executed"
    assert receipt.candidates == ()
    assert receipt.no_fake_claim_audit.fabricated_candidate_count == 0


def test_adapter_diagnoses_empty_records_and_invalid_paths(tmp_path: Path) -> None:
    # Given: an empty records file, absent lane directory, and absent records file.
    empty_request = make_request(tmp_path / "empty", DetectorLane.OWLV2_SAM2)
    empty_request.lane_output_dir.mkdir(parents=True)
    write_records(empty_request, [])
    invalid_dir_request = make_request(
        tmp_path / "invalid-dir", DetectorLane.OWLV2_SAM2
    )
    invalid_records_request = make_request(
        tmp_path / "invalid-records", DetectorLane.OWLV2_SAM2
    )
    invalid_records_request.lane_output_dir.mkdir(parents=True)

    # When: each invoked adapter normalizes its lane output.
    empty_receipt = execute_adapter(empty_request, invoked_runner)
    invalid_dir_receipt = execute_adapter(invalid_dir_request, invoked_runner)
    invalid_records_receipt = execute_adapter(invalid_records_request, invoked_runner)

    # Then: every structural failure is diagnosed without producing candidates.
    assert empty_receipt.diagnostics == ("records_empty",)
    assert invalid_dir_receipt.diagnostics == ("lane_output_dir_invalid",)
    assert invalid_records_receipt.diagnostics == ("records_json_invalid",)

    # And: an empty-but-valid execution is still real, but structural
    # failures (missing/invalid output) must not count as real execution.
    assert empty_receipt.status.value == "real_executed"
    assert invalid_dir_receipt.status.value == "failed"
    assert invalid_records_receipt.status.value == "failed"


def test_adapter_diagnoses_path_escape(tmp_path: Path) -> None:
    # Given: a record with an escaped mask path after otherwise valid fields.
    request = make_request(tmp_path, DetectorLane.OWLV2_SAM2)
    request.lane_output_dir.mkdir()
    record = make_record(request)
    record["mask_path"] = "../mask.png"
    write_records(request, [record])

    # When: the runner record is normalized.
    receipt = execute_adapter(request, invoked_runner)

    # Then: path containment rejects the record.
    assert receipt.candidates == ()
    assert receipt.diagnostics == ("mask_path_escape",)
