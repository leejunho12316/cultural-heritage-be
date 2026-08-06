"""Bounded scale-marker recognition and removal for real preprocessing."""

# pyright: reportAny=false

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from statistics import median
from typing import TYPE_CHECKING, Final

import numpy as np
from PIL import Image, ImageDraw

from modules.preprocessing.contracts.views import (
    BoundingBox,
    ScaleConfidence,
    ScaleMetadata,
)
from modules.shared import ContractValidationError

if TYPE_CHECKING:
    from pathlib import Path

    from numpy.typing import NDArray


MIN_MARKER_WIDTH_RATIO = 0.08
BOTTOM_BAND_RATIO = 0.45
MAX_REMOVAL_HEIGHT_RATIO = 0.18
MIN_DARK_RUN_PX = 3
DARK_THRESHOLD = 80
MIN_ROW_DARK_RATIO = 0.025
MAX_ROW_DARK_RATIO = 0.35
REMOVAL_PADDING_RATIO = 0.35


@dataclass(frozen=True, slots=True)
class ScaleRemovalOptions:
    """Tuning parameters for scale-marker removal."""

    padding_ratio: float = REMOVAL_PADDING_RATIO


DEFAULT_SCALE_REMOVAL_OPTIONS: Final = ScaleRemovalOptions()


@dataclass(frozen=True, slots=True)
class ScaleRemovalResult:
    """Scale preprocessing output consumed before detector execution."""

    detector_input_path: Path
    detector_input_sha256: str
    scale_metadata: ScaleMetadata
    scale_removal_applied: bool
    scale_removal_mode: str
    scale_removal_bbox: BoundingBox | None


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unavailable(reason: str) -> ScaleMetadata:
    return ScaleMetadata(
        scale_marker_detected=False,
        scale_marker_bbox=None,
        scale_marker_width_px=None,
        scale_unit_px=None,
        scale_unit_source=None,
        scale_confidence=ScaleConfidence.UNAVAILABLE,
        confidence_reasons=(reason,),
        fallback_reason="scale_marker_unavailable",
        scale_recognition_method="edge_dark_segment_v1",
    )


def _invalid_scale_state() -> ContractValidationError:
    field = "scale_marker_bbox"
    reason = "high-confidence scale marker missing bbox"
    return ContractValidationError(field, reason)


def _runs(values: NDArray[np.bool_]) -> tuple[int, ...]:
    lengths: list[int] = []
    current = 0
    for value in values:
        if bool(value):
            current += 1
        elif current:
            lengths.append(current)
            current = 0
    if current:
        lengths.append(current)
    return tuple(length for length in lengths if length >= MIN_DARK_RUN_PX)


def _row_clusters(rows: NDArray[np.int64]) -> tuple[tuple[int, int], ...]:
    clusters: list[tuple[int, int]] = []
    if len(rows) == 0:
        return ()
    start = int(rows[0])
    previous = start
    for row in rows[1:]:
        current = int(row)
        if current == previous + 1:
            previous = current
        else:
            clusters.append((start, previous + 1))
            start = current
            previous = current
    clusters.append((start, previous + 1))
    return tuple(clusters)


def _detect_scale(image: Image.Image) -> ScaleMetadata:
    rgb = np.asarray(image.convert("RGB"))
    height, width, _ = rgb.shape
    band_top = int(height * (1 - BOTTOM_BAND_RATIO))
    band = rgb[band_top:, :, :]
    dark = np.all(band < DARK_THRESHOLD, axis=2)
    row_dark_ratio = dark.mean(axis=1)
    candidate_rows = np.nonzero(
        (row_dark_ratio >= MIN_ROW_DARK_RATIO) & (row_dark_ratio <= MAX_ROW_DARK_RATIO)
    )[0]
    clusters = _row_clusters(candidate_rows)
    if not clusters:
        return _unavailable("no scale-like bottom row cluster")
    start, end = max(clusters, key=lambda cluster: cluster[1])
    _, cols = np.nonzero(dark[start:end, :])
    if len(cols) == 0:
        return _unavailable("scale-like row cluster has no dark pixels")
    left = int(cols.min())
    right = int(cols.max()) + 1
    top = start + band_top
    bottom = end + band_top
    marker_width = float(right - left)
    marker_height = float(bottom - top)
    if marker_width < width * MIN_MARKER_WIDTH_RATIO:
        return _unavailable("dark edge marker is too narrow")
    if marker_height > height * MAX_REMOVAL_HEIGHT_RATIO:
        return _unavailable("dark edge marker is too tall")
    center_row = dark[int((top + bottom) / 2) - band_top, left:right]
    runs = _runs(center_row)
    unit_px = float(median(runs)) if runs else marker_width
    bbox = BoundingBox(float(left), float(top), marker_width, marker_height)
    return ScaleMetadata(
        scale_marker_detected=True,
        scale_marker_bbox=bbox,
        scale_marker_width_px=marker_width,
        scale_unit_px=unit_px,
        scale_unit_source="detected_scale_marker_segments",
        scale_confidence=ScaleConfidence.HIGH,
        confidence_reasons=("bottom-edge dark scale segments detected",),
        fallback_reason=None,
        scale_unit_label="unknown",
        scale_unit_value=None,
        scale_marker_orientation="horizontal",
        scale_recognition_method="edge_dark_segment_v1",
    )


def _white_background() -> tuple[int, int, int]:
    return 255, 255, 255


def _expand_bbox(
    bbox: BoundingBox, image: Image.Image, options: ScaleRemovalOptions
) -> BoundingBox:
    padding_x = bbox.width * options.padding_ratio
    padding_y = bbox.height * options.padding_ratio
    left = max(bbox.left - padding_x, 0.0)
    top = max(bbox.top - padding_y, 0.0)
    right = min(bbox.left + bbox.width + padding_x, float(image.width))
    bottom = min(bbox.top + bbox.height + padding_y, float(image.height))
    return BoundingBox(left=left, top=top, width=right - left, height=bottom - top)


def prepare_detector_input(
    image_path: Path,
    run_root: Path,
    image_id: str,
    options: ScaleRemovalOptions = DEFAULT_SCALE_REMOVAL_OPTIONS,
) -> ScaleRemovalResult:
    """Detect an edge scale marker and emit the detector input image."""
    image = Image.open(image_path).convert("RGB")
    metadata = _detect_scale(image)
    detector_root = run_root / "assets" / "detector_inputs"
    detector_root.mkdir(parents=True, exist_ok=True)
    detector_input_path = detector_root / f"{image_id}.jpg"
    match metadata.scale_confidence:
        case ScaleConfidence.HIGH:
            bbox = metadata.scale_marker_bbox
            if bbox is None:
                raise _invalid_scale_state()
            removal_bbox = _expand_bbox(bbox, image, options)
            output = image.copy()
            draw = ImageDraw.Draw(output)
            box = (
                round(removal_bbox.left),
                round(removal_bbox.top),
                round(removal_bbox.left + removal_bbox.width),
                round(removal_bbox.top + removal_bbox.height),
            )
            draw.rectangle(box, fill=_white_background())
            output.save(detector_input_path)
            return ScaleRemovalResult(
                detector_input_path=detector_input_path,
                detector_input_sha256=_file_sha256(detector_input_path),
                scale_metadata=metadata,
                scale_removal_applied=True,
                scale_removal_mode="median_fill_bbox",
                scale_removal_bbox=removal_bbox,
            )
        case ScaleConfidence.MEDIUM | ScaleConfidence.LOW | ScaleConfidence.UNAVAILABLE:
            image.save(detector_input_path)
            return ScaleRemovalResult(
                detector_input_path=detector_input_path,
                detector_input_sha256=_file_sha256(detector_input_path),
                scale_metadata=metadata,
                scale_removal_applied=False,
                scale_removal_mode="not_applied",
                scale_removal_bbox=None,
            )
