from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from modules.preprocessing import (
    BoundingBox,
    CoordinateTransform,
    ViewKind,
)
from modules.rough_masking import (
    AnomalyMaskOutput,
    ImageDimensions,
    RejectedMaskOutput,
    RunnerOutcome,
    SeedRequestPaths,
    build_roi_seed_request,
    execute_adapter,
    materialize_anomaly_outputs,
)
from modules.rough_masking.artifacts.records import decode_records
from modules.rough_masking.tests.candidates.test_t4 import make_view
from modules.shared import ContractValidationError, DetectorLane

if TYPE_CHECKING:
    from pathlib import Path

    from modules.preprocessing import ViewRecord
    from modules.rough_masking import AdapterRequest


PNG_HEADER = b"\x89PNG\r\n\x1a\n"
JPEG_HEADER = b"\xff\xd8"


def make_roi_view() -> ViewRecord:
    view = make_view()
    return replace(
        view,
        kind=ViewKind.OBJECT_CROP,
        object_id="object-001",
        coordinate_transform=CoordinateTransform(
            source_bbox=BoundingBox(10.0, 12.0, 50.0, 40.0),
            restore_offset_x=10.0,
            restore_offset_y=12.0,
        ),
    )


def make_paths(tmp_path: Path, lane: DetectorLane) -> SeedRequestPaths:
    return SeedRequestPaths(
        lane_output_dir=tmp_path / lane.value,
        records_json=tmp_path / lane.value / "records.json",
        object_mask_path=make_object_mask_path(tmp_path),
    )


def make_object_mask_path(tmp_path: Path, size: tuple[int, int] = (64, 48)) -> Path:
    object_mask_path = tmp_path / "object-mask.png"
    object_mask_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("L", size, 255).save(object_mask_path, format="PNG")
    return object_mask_path


def make_roi_request(tmp_path: Path) -> AdapterRequest:
    lane = DetectorLane.OWLV2_SAM2
    return build_roi_seed_request(
        lane=lane,
        view=make_roi_view(),
        image_dimensions=ImageDimensions(64, 48),
        paths=make_paths(tmp_path, lane),
    )


def anomaly_output(request: AdapterRequest) -> AnomalyMaskOutput:
    return AnomalyMaskOutput(
        prompt=request.prompts[0],
        score=0.82,
        bbox_xyxy=(4.0, 5.0, 24.0, 28.0),
        mask_png=PNG_HEADER + b"anomaly-mask",
        overlay_jpeg=JPEG_HEADER + b"anomaly-overlay",
        quality_filter_version="rough-mask-quality-v1",
        quality_score=0.72,
        mask_area_ratio=0.08,
        bbox_fill_ratio=0.45,
        boundary_pixel_ratio=0.12,
        perimeter_coverage_ratio=0.04,
        border_touch_count=0,
        component_count=1,
        largest_component_ratio=1.0,
    )


def rejected_output(request: AdapterRequest) -> RejectedMaskOutput:
    return RejectedMaskOutput(
        prompt=request.prompts[0],
        score=0.82,
        bbox_xyxy=(4.0, 5.0, 24.0, 28.0),
        reject_reason="broad_texture_blob",
        quality_filter_version="rough-mask-quality-v1",
        quality_score=0.41,
        mask_area_ratio=0.18,
        bbox_fill_ratio=0.90,
        boundary_pixel_ratio=0.0,
        perimeter_coverage_ratio=0.0,
        border_touch_count=0,
        component_count=1,
        largest_component_ratio=1.0,
    )


def test_roi_runner_materializes_anomaly_rough_mask_candidate(
    tmp_path: Path,
) -> None:
    # Given: an object ROI request and a runner-produced anomaly mask payload.
    request = make_roi_request(tmp_path)

    def runner(request: AdapterRequest) -> RunnerOutcome:
        materialize_anomaly_outputs(request, (anomaly_output(request),))
        return RunnerOutcome(runner_invoked=True)

    # When: rough_masking executes and normalizes the lane output.
    receipt = execute_adapter(request, runner)

    # Then: the emitted rough mask is an anomaly mask, not the preprocessing ROI mask.
    candidate = receipt.candidates[0]
    assert candidate.rough_mask.relative_path == "masks/anomaly-0000.png"
    assert candidate.rough_mask.media_type == "image/png"
    assert candidate.source_object_id == "object-001"
    assert receipt.diagnostics == ()


def test_materializer_records_quality_metadata(tmp_path: Path) -> None:
    # Given: an accepted anomaly output with computed quality metrics.
    request = make_roi_request(tmp_path)

    # When: the materializer writes the runner record.
    materialize_anomaly_outputs(request, (anomaly_output(request),))

    # Then: downstream QA can inspect flat quality metadata from records.json.
    records = decode_records(request.records_json.read_text())
    assert records is not None
    assert records[0]["quality_filter_version"] == "rough-mask-quality-v1"
    assert records[0]["quality_score"] == 0.72
    assert records[0]["mask_area_ratio"] == 0.08
    assert records[0]["bbox_fill_ratio"] == 0.45
    assert records[0]["boundary_pixel_ratio"] == 0.12
    assert records[0]["perimeter_coverage_ratio"] == 0.04
    assert records[0]["border_touch_count"] == 0
    assert records[0]["component_count"] == 1
    assert records[0]["largest_component_ratio"] == 1.0


def test_materializer_records_rejected_quality_decisions(tmp_path: Path) -> None:
    # Given: a rejected anomaly output with a quality-filter reason.
    request = make_roi_request(tmp_path)

    # When: the materializer writes the runner record.
    materialize_anomaly_outputs(request, (rejected_output(request),))

    # Then: normalization can report the exact reject reason without assets.
    records = decode_records(request.records_json.read_text())
    assert records is not None
    assert records[0]["accepted"] is False
    assert records[0]["reject_reason"] == "broad_texture_blob"
    assert records[0]["quality_filter_version"] == "rough-mask-quality-v1"
    assert records[0]["mask_area_ratio"] == 0.18
    assert records[0]["perimeter_coverage_ratio"] == 0.0


def test_adapter_reports_materialized_quality_rejections(tmp_path: Path) -> None:
    # Given: a runner materializes one quality-filter rejection.
    request = make_roi_request(tmp_path)

    def runner(request: AdapterRequest) -> RunnerOutcome:
        materialize_anomaly_outputs(request, (rejected_output(request),))
        return RunnerOutcome(runner_invoked=True)

    # When: rough_masking normalizes the lane output.
    receipt = execute_adapter(request, runner)

    # Then: the rejection reason is inspectable through adapter diagnostics.
    assert receipt.candidates == ()
    assert receipt.diagnostics == ("record_rejected:broad_texture_blob",)


def test_roi_request_rejects_full_image_view(tmp_path: Path) -> None:
    # Given: a full-image view.
    lane = DetectorLane.OWLV2_SAM2

    # When / Then: ROI rough-mask requests reject non-object views.
    with pytest.raises(ContractValidationError, match="roi_view_kind"):
        _ = build_roi_seed_request(
            lane=lane,
            view=make_view(),
            image_dimensions=ImageDimensions(64, 48),
            paths=make_paths(tmp_path, lane),
        )


def test_roi_request_rejects_missing_object_mask_path(tmp_path: Path) -> None:
    # Given: an object ROI view without a readable preprocessing object mask.
    lane = DetectorLane.OWLV2_SAM2

    # When / Then: ROI rough-mask requests require foreground mask constraints.
    with pytest.raises(ContractValidationError, match="object_mask_path"):
        _ = build_roi_seed_request(
            lane=lane,
            view=make_roi_view(),
            image_dimensions=ImageDimensions(64, 48),
            paths=SeedRequestPaths(
                lane_output_dir=tmp_path / lane.value,
                records_json=tmp_path / lane.value / "records.json",
                object_mask_path=tmp_path / "missing-mask.png",
            ),
        )


def test_materializer_rejects_bbox_outside_roi_view(tmp_path: Path) -> None:
    # Given: an anomaly output whose bbox escapes the ROI view.
    request = make_roi_request(tmp_path)
    escaped = replace(anomaly_output(request), bbox_xyxy=(4.0, 5.0, 80.0, 28.0))

    # When / Then: materialization fails before a record can become a candidate.
    with pytest.raises(ContractValidationError, match="bbox_outside_roi"):
        materialize_anomaly_outputs(request, (escaped,))


def test_materializer_rejects_invalid_mask_png_signature(tmp_path: Path) -> None:
    # Given: an anomaly output with invalid mask bytes.
    request = make_roi_request(tmp_path)
    invalid = replace(anomaly_output(request), mask_png=b"not-png")

    # When / Then: materialization refuses to emit a rough-mask record.
    with pytest.raises(ContractValidationError, match="rough_mask_png"):
        materialize_anomaly_outputs(request, (invalid,))


def test_materializer_rejects_records_json_outside_lane_dir(
    tmp_path: Path,
) -> None:
    # Given: a ROI request whose records path escapes the lane output dir.
    lane = DetectorLane.OWLV2_SAM2
    request = build_roi_seed_request(
        lane=lane,
        view=make_roi_view(),
        image_dimensions=ImageDimensions(64, 48),
        paths=SeedRequestPaths(
            lane_output_dir=tmp_path / lane.value,
            records_json=tmp_path / "outside.json",
            object_mask_path=make_object_mask_path(tmp_path),
        ),
    )

    # When / Then: materialization fails before writing escaped records.
    with pytest.raises(ContractValidationError, match="records_json"):
        materialize_anomaly_outputs(request, (anomaly_output(request),))
    assert not (tmp_path / "outside.json").exists()
