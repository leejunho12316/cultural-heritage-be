"""Preprocessing ROI asset lookup for prompt-grouped mask refinement."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from modules.mask_refining.execution.models import JoinedRefinementAssets
from modules.preprocessing import (
    BoundingBox,
    CoordinateTransform,
    ScaleConfidence,
    ScaleMetadata,
    ViewKind,
    ViewRecord,
)
from modules.rag.qwen.qwen_bridge_json import JsonValue, parse_json_object
from modules.shared import ContractValidationError, ImageId

_BBOX_COORDINATES: Final = 4
_PNG_DIMENSION_HEADER_BYTES: Final = 24
_PNG_WIDTH_OFFSET: Final = 16
_PNG_HEIGHT_OFFSET: Final = 20
_JPEG_SOI: Final = b"\xff\xd8"
_JPEG_MARKER_PREFIX: Final = 0xFF
_JPEG_MARKER_BYTES: Final = 2
_JPEG_SEGMENT_LENGTH_BYTES: Final = 2
_JPEG_MIN_SOF_PAYLOAD_BYTES: Final = 5
_JPEG_SOF_HEIGHT_OFFSET: Final = 1
_JPEG_SOF_WIDTH_OFFSET: Final = 3
_JPEG_STANDALONE_MARKERS: Final = frozenset({0x01, *range(0xD0, 0xD9)})
_JPEG_SOF_MARKERS: Final = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)


class PreprocessingAssetInputError(ValueError):
    """Raised when the preprocessing manifest cannot supply an object asset index."""


@dataclass(frozen=True, slots=True)
class _ObjectAssets:
    image_id: str
    bbox_xyxy: tuple[float, float, float, float]
    roi_image_path: Path
    object_mask_path: Path


def _asset_path(raw: JsonValue | None) -> Path | None:
    if not isinstance(raw, dict):
        return None
    path = raw.get("path")
    return Path(path) if isinstance(path, str) and path.strip() else None


def _bbox(raw: JsonValue | None) -> tuple[float, float, float, float] | None:
    if not isinstance(raw, list) or len(raw) != _BBOX_COORDINATES:
        return None
    if any(
        isinstance(value, bool) or not isinstance(value, int | float) for value in raw
    ):
        return None
    numeric_values = tuple(
        float(value) for value in raw if isinstance(value, int | float)
    )
    if len(numeric_values) != _BBOX_COORDINATES:
        return None
    values = numeric_values
    left, top, right, bottom = values
    if (
        not all(math.isfinite(value) for value in values)
        or right <= left
        or bottom <= top
    ):
        return None
    return left, top, right, bottom


def _object_index(asset_root: Path) -> dict[str, _ObjectAssets]:
    path = asset_root / "manifests" / "real_preprocessing_manifest.json"
    try:
        decoded = parse_json_object(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        msg = "real_preprocessing_manifest.json missing"
        raise PreprocessingAssetInputError(msg) from error
    except ContractValidationError as error:
        msg = "real_preprocessing_manifest.json malformed"
        raise PreprocessingAssetInputError(msg) from error
    raw_objects = decoded.get("objects")
    if not isinstance(raw_objects, list):
        msg = "real_preprocessing_manifest objects missing"
        raise PreprocessingAssetInputError(msg)
    objects: dict[str, _ObjectAssets] = {}
    for raw in raw_objects:
        if not isinstance(raw, dict):
            continue
        object_id = raw.get("object_id")
        image_id = raw.get("image_id")
        bbox = _bbox(raw.get("bbox_xyxy"))
        roi_image = _asset_path(raw.get("bbox_crop"))
        object_mask = _asset_path(raw.get("mask")) or _asset_path(
            raw.get("alpha_cutout")
        )
        if (
            isinstance(object_id, str)
            and isinstance(image_id, str)
            and bbox is not None
            and roi_image is not None
            and object_mask is not None
        ):
            objects[object_id] = _ObjectAssets(image_id, bbox, roi_image, object_mask)
    return objects


def _contained_asset(path: Path, root: Path) -> Path | None:
    resolved = path.resolve()
    return (
        resolved
        if resolved.is_relative_to(root.resolve()) and resolved.is_file()
        else None
    )


def _image_dimensions(
    path: Path, bbox: tuple[float, float, float, float]
) -> tuple[int, int]:
    contents = path.read_bytes()
    parsed = _png_dimensions(contents) or _jpeg_dimensions(contents)
    if parsed is not None:
        return parsed
    left, top, right, bottom = bbox
    return math.ceil(right - left), math.ceil(bottom - top)


def _png_dimensions(contents: bytes) -> tuple[int, int] | None:
    if not (
        contents.startswith(b"\x89PNG\r\n\x1a\n")
        and len(contents) >= _PNG_DIMENSION_HEADER_BYTES
    ):
        return None
    width = int.from_bytes(contents[_PNG_WIDTH_OFFSET:_PNG_HEIGHT_OFFSET], "big")
    height = int.from_bytes(
        contents[_PNG_HEIGHT_OFFSET:_PNG_DIMENSION_HEADER_BYTES], "big"
    )
    return (width, height) if width > 0 and height > 0 else None


def _jpeg_dimensions(contents: bytes) -> tuple[int, int] | None:
    if not contents.startswith(_JPEG_SOI):
        return None
    index = len(_JPEG_SOI)
    while index + _JPEG_MARKER_BYTES < len(contents):
        if contents[index] != _JPEG_MARKER_PREFIX:
            index += 1
            continue
        marker = contents[index + 1]
        index += _JPEG_MARKER_BYTES
        if marker == _JPEG_MARKER_PREFIX:
            continue
        if marker in _JPEG_STANDALONE_MARKERS:
            continue
        if index + _JPEG_SEGMENT_LENGTH_BYTES > len(contents):
            return None
        segment_length = int.from_bytes(
            contents[index : index + _JPEG_SEGMENT_LENGTH_BYTES], "big"
        )
        payload_start = index + _JPEG_SEGMENT_LENGTH_BYTES
        payload_end = index + segment_length
        if payload_end > len(contents) or segment_length < _JPEG_SEGMENT_LENGTH_BYTES:
            return None
        if marker in _JPEG_SOF_MARKERS:
            if payload_start + _JPEG_MIN_SOF_PAYLOAD_BYTES > payload_end:
                return None
            height_start = payload_start + _JPEG_SOF_HEIGHT_OFFSET
            width_start = payload_start + _JPEG_SOF_WIDTH_OFFSET
            height = int.from_bytes(contents[height_start : height_start + 2], "big")
            width = int.from_bytes(contents[width_start : width_start + 2], "big")
            return (width, height) if width > 0 and height > 0 else None
        index = payload_end
    return None


def join_preprocessing_assets(
    asset_root: Path, object_id: str, expected_image_id: str
) -> JoinedRefinementAssets | None:
    """Resolve one rough candidate object to its contained ROI image and mask."""
    raw = _object_index(asset_root).get(object_id)
    if raw is None or raw.image_id != expected_image_id:
        return None
    roi_image = _contained_asset(raw.roi_image_path, asset_root)
    object_mask = _contained_asset(raw.object_mask_path, asset_root)
    if roi_image is None or object_mask is None:
        return None
    dimensions = _image_dimensions(roi_image, raw.bbox_xyxy)
    left, top, right, bottom = raw.bbox_xyxy
    crop_left = float(round(left))
    crop_top = float(round(top))
    crop_right = float(round(right))
    crop_bottom = float(round(bottom))
    view = ViewRecord(
        view_id=f"refinement-object:{object_id}",
        kind=ViewKind.OBJECT_CROP,
        image_id=ImageId(raw.image_id),
        object_id=object_id,
        tile_view_id=None,
        source_view_id=f"preprocessing-object:{object_id}",
        rag_followup_view_id=None,
        view_reuse_mode=None,
        coordinate_transform=CoordinateTransform(
            BoundingBox(
                crop_left, crop_top, crop_right - crop_left, crop_bottom - crop_top
            ),
            crop_left,
            crop_top,
        ),
        scale_metadata=ScaleMetadata(
            scale_marker_detected=False,
            scale_marker_bbox=None,
            scale_marker_width_px=None,
            scale_unit_px=None,
            scale_unit_source=None,
            scale_confidence=ScaleConfidence.UNAVAILABLE,
            confidence_reasons=("refinement_roi",),
            fallback_reason="not_required",
        ),
    )
    return JoinedRefinementAssets(object_id, roi_image, object_mask, *dimensions, view)
