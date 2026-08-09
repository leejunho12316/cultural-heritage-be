from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

from modules.preprocessing.model_runtime.inventory import ModelInventoryEntry
from modules.rough_masking import ImageDimensions, build_roi_seed_request
from modules.rough_masking.artifacts.materialization import AnomalyMaskOutput
from modules.rough_masking.local_model.segmentation import (
    LocalDetection,
    LocalInferenceSettings,
    segment_detections,
)
from modules.rough_masking.tests.artifacts.test_t4_generation import (
    make_object_mask_path,
    make_paths,
    make_roi_view,
)
from modules.shared import DetectorLane

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from numpy.typing import NDArray

    from modules.rough_masking.artifacts.materialization import MaskOutput


class StaticMaskPredictor:
    def __init__(self, mask: NDArray[np.bool_]) -> None:
        self.mask: NDArray[np.bool_] = mask

    def set_image(self, image: NDArray[np.uint8]) -> None:
        assert image.shape == (*self.mask.shape, 3)

    def predict(
        self, *, box: NDArray[np.float32], multimask_output: bool
    ) -> tuple[NDArray[np.bool_], NDArray[np.float32], NDArray[np.float32]]:
        assert box.shape == (4,)
        assert multimask_output is False
        return (
            np.asarray([self.mask], dtype=np.bool_),
            np.asarray([1.0], dtype=np.float32),
            np.asarray([0.0], dtype=np.float32),
        )


def make_settings(tmp_path: Path) -> LocalInferenceSettings:
    object_mask_path = make_object_mask_path(tmp_path, (20, 20))
    return LocalInferenceSettings(
        ModelInventoryEntry("detector", "unused/repo", "main", tmp_path / "detector"),
        ModelInventoryEntry("sam2", "unused/repo", "main", tmp_path / "sam2"),
        "mps",
        1.0,
        object_mask_path,
        (0.0, 0.0, 20.0, 20.0),
    )


def run_static_mask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mask: NDArray[np.bool_]
) -> tuple[MaskOutput, ...]:
    settings = make_settings(tmp_path)

    def fake_sam2_predictor(settings: LocalInferenceSettings) -> StaticMaskPredictor:
        _ = settings
        return StaticMaskPredictor(mask)

    monkeypatch.setattr(
        "modules.rough_masking.local_model.segmentation.load_sam2_predictor",
        fake_sam2_predictor,
    )
    request = build_roi_seed_request(
        lane=DetectorLane.OWLV2_SAM2,
        view=make_roi_view(),
        image_dimensions=ImageDimensions(20, 20),
        paths=make_paths(tmp_path / "run", DetectorLane.OWLV2_SAM2),
    )
    return segment_detections(
        (LocalDetection(request.prompts[0], 0.9, (0.0, 0.0, 20.0, 20.0)),),
        Image.new("RGB", (20, 20)),
        settings,
    )


def test_compact_sam2_mask_records_quality_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a compact interior mask that is plausible as a rough anomaly region.
    mask = np.zeros((20, 20), dtype=np.bool_)
    mask[8:12, 8:12] = True

    # When: segmentation accepts the compact mask.
    outputs = run_static_mask(tmp_path, monkeypatch, mask)

    # Then: quality metadata is attached for downstream filtering and QA.
    assert len(outputs) == 1
    output = outputs[0]
    assert isinstance(output, AnomalyMaskOutput)
    assert output.quality_filter_version == "rough-mask-quality-v1"
    assert output.mask_area_ratio == 0.04
    assert output.bbox_fill_ratio == 1.0
    assert output.boundary_pixel_ratio == 0.0
    assert output.perimeter_coverage_ratio == 0.0
    assert output.border_touch_count == 0
    assert output.component_count == 1
    assert output.largest_component_ratio == 1.0
    assert 0.0 < output.quality_score <= 1.0


def test_broad_texture_blob_mask_is_recorded_as_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a broad filled interior blob below the hard max area threshold.
    mask = np.zeros((20, 20), dtype=np.bool_)
    mask[4:12, 4:12] = True

    # When: segmentation evaluates mask quality.
    outputs = run_static_mask(tmp_path, monkeypatch, mask)

    # Then: broad texture-like blobs are recorded as rejected before materialization.
    assert len(outputs) == 1
    output = outputs[0]
    assert not isinstance(output, AnomalyMaskOutput)
    assert output.reject_reason == "broad_texture_blob"
    assert output.perimeter_coverage_ratio == 0.0


def test_roi_frame_mask_is_recorded_as_edge_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a one-pixel ROI frame artifact with no interior anomaly mass.
    mask = np.zeros((20, 20), dtype=np.bool_)
    mask[0, :] = True
    mask[-1, :] = True
    mask[:, 0] = True
    mask[:, -1] = True

    # When: segmentation evaluates mask quality.
    outputs = run_static_mask(tmp_path, monkeypatch, mask)

    # Then: ROI-frame artifacts are recorded as rejected edge artifacts.
    assert len(outputs) == 1
    output = outputs[0]
    assert not isinstance(output, AnomalyMaskOutput)
    assert output.reject_reason == "edge_artifact"
    assert output.perimeter_coverage_ratio == 0.0


def test_multi_border_edge_artifact_mask_is_recorded_as_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an L-shaped edge artifact touching multiple ROI borders.
    mask = np.zeros((20, 20), dtype=np.bool_)
    mask[0:3, :] = True
    mask[:, 0:3] = True

    # When: segmentation evaluates mask quality.
    outputs = run_static_mask(tmp_path, monkeypatch, mask)

    # Then: multi-border edge artifacts are recorded as rejected.
    assert len(outputs) == 1
    output = outputs[0]
    assert not isinstance(output, AnomalyMaskOutput)
    assert output.reject_reason == "edge_artifact"
    assert output.perimeter_coverage_ratio == 0.0
