from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageDraw

from modules import preprocessing
from modules.preprocessing.detection.scale_marker import prepare_detector_input

if TYPE_CHECKING:
    from pathlib import Path


def _scale_fixture(path: Path) -> Path:
    image = Image.new("RGB", (240, 160), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((60, 40, 180, 90), fill=(180, 120, 80))
    x = 70
    for index in range(6):
        fill = "black" if index % 2 == 0 else "white"
        draw.rectangle((x, 130, x + 14, 140), fill=fill)
        x += 14
    image.save(path)
    return path


def test_scale_marker_detects_bottom_edge_bar_and_removes_it(tmp_path: Path) -> None:
    # Given: an image with a clear black-white scale bar near the bottom edge.
    image_path = _scale_fixture(tmp_path / "scaled.jpg")
    before = np.asarray(Image.open(image_path).convert("RGB"))

    # When: the detector input is prepared before model execution.
    result = prepare_detector_input(image_path, tmp_path / "run", "image-001")

    # Then: scale metadata is recorded and only the scale region is filled.
    after_image = Image.open(result.detector_input_path).convert("RGB")
    after = np.asarray(after_image)
    assert result.scale_removal_applied is True
    assert result.scale_metadata.scale_marker_detected is True
    assert result.scale_metadata.scale_unit_px is not None
    assert result.scale_metadata.scale_unit_label == "unknown"
    assert after_image.size == (240, 160)
    assert not np.array_equal(before[132:138, 72:126], after[132:138, 72:126])
    central_delta = np.abs(
        before[50:80, 80:160].astype(np.int16) - after[50:80, 80:160].astype(np.int16)
    )
    assert central_delta.mean() < 1.0


def test_scale_marker_noops_when_marker_is_unavailable(tmp_path: Path) -> None:
    # Given: an image with no scale marker.
    image_path = tmp_path / "plain.jpg"
    Image.new("RGB", (240, 160), "white").save(image_path)

    # When: the detector input is prepared.
    result = prepare_detector_input(image_path, tmp_path / "run", "image-001")

    # Then: scale metadata records unavailable confidence and removal is skipped.
    assert result.scale_removal_applied is False
    assert result.scale_metadata.scale_marker_detected is False
    assert (
        result.scale_metadata.scale_confidence
        is preprocessing.ScaleConfidence.UNAVAILABLE
    )
    assert result.detector_input_path.is_file()
