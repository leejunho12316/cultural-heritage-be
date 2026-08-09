from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from modules.anomaly_grouping.models import BoundingBox
from modules.anomaly_grouping.runner import main
from modules.anomaly_grouping.tests.mask_test_support import rect_mask

if TYPE_CHECKING:
    from pathlib import Path

    from modules.anomaly_grouping.shared_contracts import JsonObject


def test_runner_writes_deterministic_result_json(tmp_path: Path) -> None:
    # Given: a minimal valid runner request.
    request_path = tmp_path / "request.json"
    output_path = tmp_path / "result.json"
    mask = rect_mask(tmp_path, "candidate-a", BoundingBox(0, 0, 100, 100))
    _ = request_path.write_text(
        json.dumps(
            {
                "schema": "anomaly_grouping_request_v1",
                "mask_output_dir": str(tmp_path / "masks"),
                "candidates": [
                    {
                        "candidate_id": "candidate-a",
                        "image_id": "image-1",
                        "source_object_id": "object-1",
                        "source_view_id": "view-1",
                        "seed_lane": "owlv2_sam2",
                        "seed_prompt": "crack",
                        "bbox_xyxy": [0, 0, 100, 100],
                        "mask": {"path": mask.path, "sha256": mask.sha256},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    # When: the standalone runner processes the request.
    exit_code = main(
        ("--input-json", str(request_path), "--output-json", str(output_path))
    )

    # Then: a deterministic result artifact is written.
    assert exit_code == 0
    payload = cast("JsonObject", json.loads(output_path.read_text(encoding="utf-8")))
    assert payload["schema"] == "anomaly_grouping_result_v1"
    assert payload["followup_parent_targets"] == [
        {
            "candidate_id": "candidate-a",
            "selector_id": "candidate-a",
            "selector_type": "candidate_id",
        }
    ]


def test_runner_returns_two_for_malformed_request(tmp_path: Path) -> None:
    # Given: a request with the wrong schema marker.
    request_path = tmp_path / "request.json"
    output_path = tmp_path / "result.json"
    _ = request_path.write_text('{"schema":"wrong"}', encoding="utf-8")

    # When: the standalone runner parses it.
    exit_code = main(
        ("--input-json", str(request_path), "--output-json", str(output_path))
    )

    # Then: invalid input maps to the pipeline failure exit code.
    assert exit_code == 2
    assert not output_path.exists()


def test_runner_returns_two_for_missing_mask(tmp_path: Path) -> None:
    # Given: the request schema is valid but a candidate has no mask field.
    request_path = tmp_path / "request.json"
    output_path = tmp_path / "result.json"
    _ = request_path.write_text(
        json.dumps(
            {
                "schema": "anomaly_grouping_request_v1",
                "mask_output_dir": str(tmp_path / "masks"),
                "candidates": [
                    {
                        "candidate_id": "candidate-a",
                        "image_id": "image-1",
                        "source_object_id": "object-1",
                        "source_view_id": "view-1",
                        "seed_lane": "owlv2_sam2",
                        "seed_prompt": "crack",
                        "bbox_xyxy": [0, 0, 10, 10],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    # When: the runner parses it.
    exit_code = main(
        ("--input-json", str(request_path), "--output-json", str(output_path))
    )

    # Then: a candidate without mask data fails closed (mask is mandatory).
    assert exit_code == 2
    assert not output_path.exists()


def test_runner_returns_two_for_malformed_rag_status_enum(tmp_path: Path) -> None:
    # Given: candidate evidence contains an unsupported RAG accounting status.
    request_path = tmp_path / "request.json"
    output_path = tmp_path / "result.json"
    mask = rect_mask(tmp_path, "candidate-a", BoundingBox(0, 0, 10, 10))
    _ = request_path.write_text(
        json.dumps(
            {
                "schema": "anomaly_grouping_request_v1",
                "mask_output_dir": str(tmp_path / "masks"),
                "candidates": [
                    {
                        "candidate_id": "candidate-a",
                        "image_id": "image-1",
                        "source_object_id": "object-1",
                        "source_view_id": "view-1",
                        "seed_lane": "owlv2_sam2",
                        "seed_prompt": "crack",
                        "bbox_xyxy": [0, 0, 10, 10],
                        "mask": {"path": mask.path, "sha256": mask.sha256},
                        "evidence": {"rag_status": "bogus"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    # When: the runner parses it.
    exit_code = main(
        ("--input-json", str(request_path), "--output-json", str(output_path))
    )

    # Then: malformed enum input maps to exit code two.
    assert exit_code == 2
    assert not output_path.exists()


def test_runner_returns_two_for_boolean_bbox_coordinates(tmp_path: Path) -> None:
    # Given: JSON booleans appear in a numeric bbox field.
    request_path = tmp_path / "request.json"
    output_path = tmp_path / "result.json"
    _ = request_path.write_text(
        json.dumps(
            {
                "schema": "anomaly_grouping_request_v1",
                "mask_output_dir": str(tmp_path / "masks"),
                "candidates": [
                    {
                        "candidate_id": "candidate-a",
                        "image_id": "image-1",
                        "source_object_id": "object-1",
                        "source_view_id": "view-1",
                        "seed_lane": "owlv2_sam2",
                        "seed_prompt": "crack",
                        "bbox_xyxy": [False, False, True, True],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    # When: the runner parses the malformed numeric boundary.
    exit_code = main(
        ("--input-json", str(request_path), "--output-json", str(output_path))
    )

    # Then: boolean numeric input fails closed.
    assert exit_code == 2
    assert not output_path.exists()


def test_runner_returns_two_for_boolean_visual_cue_confidence(
    tmp_path: Path,
) -> None:
    # Given: JSON boolean appears in a float evidence field.
    request_path = tmp_path / "request.json"
    output_path = tmp_path / "result.json"
    mask = rect_mask(tmp_path, "candidate-a", BoundingBox(0, 0, 10, 10))
    _ = request_path.write_text(
        json.dumps(
            {
                "schema": "anomaly_grouping_request_v1",
                "mask_output_dir": str(tmp_path / "masks"),
                "candidates": [
                    {
                        "candidate_id": "candidate-a",
                        "image_id": "image-1",
                        "source_object_id": "object-1",
                        "source_view_id": "view-1",
                        "seed_lane": "owlv2_sam2",
                        "seed_prompt": "crack",
                        "bbox_xyxy": [0, 0, 10, 10],
                        "mask": {"path": mask.path, "sha256": mask.sha256},
                        "evidence": {"visual_cue_confidence": True},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    # When: the runner parses the malformed numeric boundary.
    exit_code = main(
        ("--input-json", str(request_path), "--output-json", str(output_path))
    )

    # Then: boolean numeric input fails closed.
    assert exit_code == 2
    assert not output_path.exists()
