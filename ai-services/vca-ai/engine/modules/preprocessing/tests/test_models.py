from __future__ import annotations

# pyright: reportAny=false
from typing import TYPE_CHECKING

import pytest
import torch
from PIL import Image
from torch import Tensor

from modules.preprocessing.contracts.records import DetectionRun
from modules.preprocessing.model_runtime.models import (
    ImageProcessorSettings,
    detect_boxes,
)
from modules.prompt_generating import static_seed_minimal_pack

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


class FakeProcessor:
    image_processor: ImageProcessorSettings

    def __init__(self) -> None:
        self.image_processor = FakeImageProcessor()

    def __call__(
        self, *, text: list[list[str]], images: Image.Image, return_tensors: str
    ) -> dict[str, Tensor]:
        _ = text, images, return_tensors
        return {"pixel_values": torch.tensor([1.0])}

    def post_process_grounded_object_detection(
        self,
        *,
        outputs: Tensor,
        target_sizes: Tensor,
        threshold: float,
        text_labels: list[list[str]],
    ) -> list[dict[str, Tensor]]:
        _ = outputs, target_sizes, threshold, text_labels
        return [
            {
                "scores": torch.tensor([0.6, 0.9, 0.8]),
                "boxes": torch.tensor(
                    [
                        [1.0, 1.0, 4.0, 4.0],
                        [2.0, 2.0, 6.0, 6.0],
                        [3.0, 3.0, 8.0, 8.0],
                    ]
                ),
            }
        ]


class FakeModel:
    def __init__(self) -> None:
        self.calls: list[dict[str, Tensor | bool]] = []

    def __call__(self, **inputs: Tensor | bool) -> Tensor:
        self.calls.append(inputs)
        return torch.tensor([0.0])

    def parameters(self) -> Iterator[Tensor]:
        yield torch.tensor([0.0])


class FakeImageProcessor:
    size: dict[str, int]

    def __init__(self) -> None:
        self.size = {"height": 960, "width": 960}


def _write_image(path: Path) -> Path:
    Image.new("RGB", (10, 10)).save(path)
    return path


def _detection_run(
    max_detections: int | None,
    detector_input_size: int = 960,
) -> DetectionRun[FakeProcessor, FakeModel]:
    return DetectionRun(
        processor=FakeProcessor(),
        model=FakeModel(),
        prompts=(static_seed_minimal_pack.records[0],),
        threshold=0.05,
        max_detections=max_detections,
        detector_input_size=detector_input_size,
    )


def test_detect_boxes_returns_all_valid_thresholded_boxes_when_unlimited(
    tmp_path: Path,
) -> None:
    # Given: a thresholded OWLv2 result with no explicit detection cap.
    image_path = _write_image(tmp_path / "artifact.png")

    # When: detector boxes are materialized.
    detections = detect_boxes(image_path, _detection_run(None))

    # Then: every valid box is returned in descending score order.
    actual_scores = [detection.score for detection in detections]
    assert actual_scores == pytest.approx([0.9, 0.8, 0.6])


def test_detect_boxes_returns_top_n_valid_thresholded_boxes_when_capped(
    tmp_path: Path,
) -> None:
    # Given: a thresholded OWLv2 result with an explicit two-box cap.
    image_path = _write_image(tmp_path / "artifact.png")

    # When: detector boxes are materialized.
    detections = detect_boxes(image_path, _detection_run(2))

    # Then: only the highest-scoring valid boxes are returned.
    assert [detection.score for detection in detections] == pytest.approx([0.9, 0.8])


def test_detect_boxes_returns_no_boxes_when_capped_at_zero(tmp_path: Path) -> None:
    # Given: a thresholded OWLv2 result with a zero-box cap.
    image_path = _write_image(tmp_path / "artifact.png")

    # When: detector boxes are materialized.
    detections = detect_boxes(image_path, _detection_run(0))

    # Then: the explicit cap prevents every detection.
    assert detections == ()


def test_detect_boxes_interpolates_enlarged_detector_input_size(
    tmp_path: Path,
) -> None:
    # Given: an enlarged OWLv2 detector input size.
    image_path = _write_image(tmp_path / "artifact.png")
    detection_run = _detection_run(None, detector_input_size=1152)

    # When: detector boxes are materialized.
    _ = detect_boxes(image_path, detection_run)

    # Then: default detection resizes the processor and enables interpolation.
    assert detection_run.processor.image_processor.size == {
        "height": 1152,
        "width": 1152,
    }
    assert detection_run.model.calls[0]["interpolate_pos_encoding"] is True


def test_detect_boxes_preserves_native_detector_input_size(
    tmp_path: Path,
) -> None:
    # Given: the native OWLv2 checkpoint detector input size.
    image_path = _write_image(tmp_path / "artifact.png")
    detection_run = _detection_run(None, detector_input_size=960)

    # When: detector boxes are materialized.
    _ = detect_boxes(image_path, detection_run)

    # Then: native detection uses the checkpoint size without interpolation.
    assert detection_run.processor.image_processor.size == {"height": 960, "width": 960}
    assert "interpolate_pos_encoding" not in detection_run.model.calls[0]


def test_detect_boxes_interpolates_non_default_detector_input_size(
    tmp_path: Path,
) -> None:
    # Given: a larger square OWLv2 detector input size.
    image_path = _write_image(tmp_path / "artifact.png")
    detection_run = _detection_run(None, detector_input_size=1152)

    # When: detector boxes are materialized.
    _ = detect_boxes(image_path, detection_run)

    # Then: detection resizes the processor and enables position interpolation.
    assert detection_run.processor.image_processor.size == {
        "height": 1152,
        "width": 1152,
    }
    assert detection_run.model.calls[0]["interpolate_pos_encoding"] is True
