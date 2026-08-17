from __future__ import annotations

from io import BytesIO
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
    JPEG_HEADER,
    PNG_HEADER,
    make_object_mask_path,
    make_paths,
    make_roi_request,
    make_roi_view,
)
from modules.rough_masking.tests.local_model.test_runtime_fakes import (
    SmallAndLargeMaskPredictor,
)
from modules.shared import DetectorLane

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from numpy.typing import NDArray


def test_sam2_masks_define_outputs_and_filter_excessive_area(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a fake SAM2 predictor emitting one small and one full-ROI mask.
    request = build_roi_seed_request(
        lane=DetectorLane.OWLV2_SAM2,
        view=make_roi_view(),
        image_dimensions=ImageDimensions(10, 8),
        paths=make_paths(tmp_path / "run", DetectorLane.OWLV2_SAM2),
    )
    object_mask_path = request.object_mask_path
    assert object_mask_path is not None
    settings = LocalInferenceSettings(
        ModelInventoryEntry("detector", "unused/repo", "main", tmp_path / "detector"),
        ModelInventoryEntry("sam2", "unused/repo", "main", tmp_path / "sam2"),
        "mps",
        request.threshold_config.max_mask_area_ratio,
        object_mask_path,
        (0.0, 0.0, 10.0, 8.0),
    )

    def fake_sam2_predictor(
        settings: LocalInferenceSettings,
    ) -> SmallAndLargeMaskPredictor:
        _ = settings
        return SmallAndLargeMaskPredictor()

    monkeypatch.setattr(
        "modules.rough_masking.local_model.segmentation.load_sam2_predictor",
        fake_sam2_predictor,
    )
    detections = (
        LocalDetection(request.prompts[0], 0.9, (1.0, 1.0, 4.0, 4.0)),
        LocalDetection(request.prompts[0], 0.8, (5.0, 1.0, 8.0, 4.0)),
    )

    # When: SAM2 receives the local detector boxes.
    outputs = segment_detections(detections, Image.new("RGB", (10, 8)), settings)

    # Then: only the fake SAM2 small mask becomes valid PNG and JPEG artifacts.
    accepted = tuple(
        output for output in outputs if isinstance(output, AnomalyMaskOutput)
    )
    rejected = tuple(
        output for output in outputs if not isinstance(output, AnomalyMaskOutput)
    )
    assert len(accepted) == 1
    assert len(rejected) == 1
    assert rejected[0].reject_reason == "max_area"
    assert accepted[0].bbox_xyxy == (1.0, 1.0, 4.0, 4.0)
    assert accepted[0].mask_png.startswith(PNG_HEADER)
    assert accepted[0].overlay_jpeg.startswith(JPEG_HEADER)
    with Image.open(BytesIO(accepted[0].mask_png)) as mask_image:
        assert mask_image.getpixel((0, 0)) == 255
    with Image.open(BytesIO(accepted[0].overlay_jpeg)) as overlay_image:
        assert overlay_image.format == "JPEG"


def test_sam2_masks_are_clipped_to_object_foreground(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: SAM2 covers the whole ROI while the object mask marks only one pixel.
    object_mask_path = make_object_mask_path(tmp_path, (10, 8))
    Image.new("L", (10, 8), 0).save(object_mask_path, format="PNG")
    with Image.open(object_mask_path) as object_mask:
        editable_mask = object_mask.copy()
    editable_mask.putpixel((2, 1), 255)
    editable_mask.save(object_mask_path, format="PNG")
    settings = LocalInferenceSettings(
        ModelInventoryEntry("detector", "unused/repo", "main", tmp_path / "detector"),
        ModelInventoryEntry("sam2", "unused/repo", "main", tmp_path / "sam2"),
        "mps",
        1.0,
        object_mask_path,
        (0.0, 0.0, 10.0, 8.0),
    )

    class FullMaskPredictor:
        def set_image(self, image: NDArray[np.uint8]) -> None:
            assert image.shape == (8, 10, 3)

        def predict(
            self, *, box: NDArray[np.float32], multimask_output: bool
        ) -> tuple[NDArray[np.bool_], NDArray[np.float32], NDArray[np.float32]]:
            assert box.shape == (4,)
            assert multimask_output is False
            return (
                np.ones((1, 8, 10), dtype=np.bool_),
                np.asarray([1.0], dtype=np.float32),
                np.asarray([0.0], dtype=np.float32),
            )

    def fake_sam2_predictor(settings: LocalInferenceSettings) -> FullMaskPredictor:
        _ = settings
        return FullMaskPredictor()

    monkeypatch.setattr(
        "modules.rough_masking.local_model.segmentation.load_sam2_predictor",
        fake_sam2_predictor,
    )
    request = build_roi_seed_request(
        lane=DetectorLane.OWLV2_SAM2,
        view=make_roi_view(),
        image_dimensions=ImageDimensions(10, 8),
        paths=make_paths(tmp_path / "run", DetectorLane.OWLV2_SAM2),
    )

    # When: the final anomaly mask is encoded.
    outputs = segment_detections(
        (LocalDetection(request.prompts[0], 0.9, (0.0, 0.0, 10.0, 8.0)),),
        Image.new("RGB", (10, 8)),
        settings,
    )

    # Then: only the object foreground intersection remains visible.
    output = outputs[0]
    assert isinstance(output, AnomalyMaskOutput)
    with Image.open(BytesIO(output.mask_png)) as mask_image:
        assert mask_image.getpixel((2, 1)) == 255
        assert mask_image.getpixel((0, 0)) == 0


def test_sam2_masks_with_empty_object_intersection_are_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a valid SAM2 mask and an empty object foreground mask.
    object_mask_path = make_object_mask_path(tmp_path, (10, 8))
    Image.new("L", (10, 8), 0).save(object_mask_path, format="PNG")
    settings = LocalInferenceSettings(
        ModelInventoryEntry("detector", "unused/repo", "main", tmp_path / "detector"),
        ModelInventoryEntry("sam2", "unused/repo", "main", tmp_path / "sam2"),
        "mps",
        1.0,
        object_mask_path,
        (0.0, 0.0, 10.0, 8.0),
    )

    def fake_sam2_predictor(
        settings: LocalInferenceSettings,
    ) -> SmallAndLargeMaskPredictor:
        _ = settings
        return SmallAndLargeMaskPredictor()

    monkeypatch.setattr(
        "modules.rough_masking.local_model.segmentation.load_sam2_predictor",
        fake_sam2_predictor,
    )

    # When: SAM2 output has no foreground overlap.
    outputs = segment_detections(
        (
            LocalDetection(
                make_roi_request(tmp_path / "prompt").prompts[0],
                0.9,
                (1.0, 1.0, 4.0, 4.0),
            ),
        ),
        Image.new("RGB", (10, 8)),
        settings,
    )

    # Then: no anomaly candidate is materialized from background-only pixels.
    assert outputs == ()
