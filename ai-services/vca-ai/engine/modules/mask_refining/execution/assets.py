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
_WEBP_RIFF_HEADER: Final = b"RIFF"
_WEBP_FOURCC: Final = b"WEBP"
_WEBP_RIFF_HEADER_BYTES: Final = 12
_WEBP_CHUNK_HEADER_BYTES: Final = 8
_WEBP_VP8_START_CODE: Final = b"\x9d\x01\x2a"
_WEBP_VP8_PAYLOAD_MIN_BYTES: Final = 10
_WEBP_VP8L_SIGNATURE_BYTE: Final = 0x2F
_WEBP_VP8L_PAYLOAD_MIN_BYTES: Final = 5
_WEBP_VP8X_PAYLOAD_MIN_BYTES: Final = 10
_TIFF_LE_BYTE_ORDER: Final = b"II"
_TIFF_BE_BYTE_ORDER: Final = b"MM"
_TIFF_MAGIC: Final = 42
_TIFF_WIDTH_TAG: Final = 256
_TIFF_HEIGHT_TAG: Final = 257
_TIFF_SHORT_TYPE: Final = 3
_TIFF_LONG_TYPE: Final = 4
_TIFF_IFD_ENTRY_BYTES: Final = 12


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


# manifest의 bbox_xyxy 배열이 숫자 4개이고, 유한값이며, right>left,
# bottom>top인 정상적인 상자인지 검증한다. 형식이 어긋나면 None을
# 반환해 해당 object를 조용히 건너뛰게 한다.
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


# real_preprocessing_manifest.json을 읽어 object_id -> ROI 이미지/마스크
# 경로 매핑을 만든다. join_preprocessing_assets가 rough candidate를
# preprocessing 산출물에 연결할 때 이 인덱스를 조회한다.
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


# 경로가 asset root 밖으로 벗어나지 않고 실제 파일로 존재하는지
# 확인한다. root를 벗어나면 None을 반환해 경로 이탈을 차단한다.
def _contained_asset(path: Path, root: Path) -> Path | None:
    resolved = path.resolve()
    return (
        resolved
        if resolved.is_relative_to(root.resolve()) and resolved.is_file()
        else None
    )


# PIL을 쓰지 않고 원시 바이트에서 직접 크기를 파싱한다. 이렇게 해야
# 실제 refinement 실행이 필요해지기 전까지 이 초기 자산 조회 경로가
# 무거운 이미지 라이브러리 import 없이 가볍게 유지된다. 파싱에 실패하면
# bbox 크기로 대체한다.
def _image_dimensions(
    path: Path, bbox: tuple[float, float, float, float]
) -> tuple[int, int]:
    contents = path.read_bytes()
    parsed = _decoded_dimensions(contents)
    if parsed is not None:
        return parsed
    left, top, right, bottom = bbox
    return math.ceil(right - left), math.ceil(bottom - top)


def _decoded_dimensions(contents: bytes) -> tuple[int, int] | None:
    return (
        _png_dimensions(contents)
        or _jpeg_dimensions(contents)
        or _webp_dimensions(contents)
        or _tiff_dimensions(contents)
    )


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


# JPEG 마커를 순회하며 SOF(Start Of Frame) 세그먼트를 찾아 너비/높이를
# 읽는다. 세그먼트가 잘려 있거나 형식이 어긋나면 None을 반환한다.
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


# RIFF 컨테이너를 순회하며 VP8(손실)/VP8L(무손실)/VP8X(확장, alpha·exif
# 등을 담을 때 사용) 청크 중 크기 정보가 있는 걸 찾는다. 세 서브포맷 모두
# 헤더 레이아웃이 달라 각자 파싱한다.
def _webp_dimensions(contents: bytes) -> tuple[int, int] | None:
    if (
        len(contents) < _WEBP_RIFF_HEADER_BYTES
        or not contents.startswith(_WEBP_RIFF_HEADER)
        or contents[8:12] != _WEBP_FOURCC
    ):
        return None
    index = _WEBP_RIFF_HEADER_BYTES
    while index + _WEBP_CHUNK_HEADER_BYTES <= len(contents):
        fourcc = contents[index : index + 4]
        chunk_size = int.from_bytes(
            contents[index + 4 : index + _WEBP_CHUNK_HEADER_BYTES], "little"
        )
        payload_start = index + _WEBP_CHUNK_HEADER_BYTES
        payload_end = payload_start + chunk_size
        if payload_end > len(contents):
            return None
        dimensions = _webp_chunk_dimensions(fourcc, contents[payload_start:payload_end])
        if dimensions is not None:
            return dimensions
        # RIFF chunks are padded to an even byte count.
        index = payload_end + (chunk_size % 2)
    return None


def _webp_chunk_dimensions(fourcc: bytes, payload: bytes) -> tuple[int, int] | None:
    if fourcc == b"VP8X" and len(payload) >= _WEBP_VP8X_PAYLOAD_MIN_BYTES:
        width = int.from_bytes(payload[4:7], "little") + 1
        height = int.from_bytes(payload[7:10], "little") + 1
        return (width, height) if width > 0 and height > 0 else None
    if (
        fourcc == b"VP8L"
        and len(payload) >= _WEBP_VP8L_PAYLOAD_MIN_BYTES
        and payload[0] == _WEBP_VP8L_SIGNATURE_BYTE
    ):
        bits = int.from_bytes(payload[1:5], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return (width, height) if width > 0 and height > 0 else None
    if (
        fourcc == b"VP8 "
        and len(payload) >= _WEBP_VP8_PAYLOAD_MIN_BYTES
        and payload[3:6] == _WEBP_VP8_START_CODE
    ):
        width = int.from_bytes(payload[6:8], "little") & 0x3FFF
        height = int.from_bytes(payload[8:10], "little") & 0x3FFF
        return (width, height) if width > 0 and height > 0 else None
    return None


_TIFF_HEADER_BYTES: Final = 8


def _tiff_byte_order(contents: bytes) -> str | None:
    if len(contents) < _TIFF_HEADER_BYTES:
        return None
    byte_order = contents[0:2]
    if byte_order == _TIFF_LE_BYTE_ORDER:
        endian = "little"
    elif byte_order == _TIFF_BE_BYTE_ORDER:
        endian = "big"
    else:
        return None
    return endian if int.from_bytes(contents[2:4], endian) == _TIFF_MAGIC else None


# 12바이트 IFD 엔트리 하나를 읽어 ImageWidth(256)/ImageLength(257) 태그면
# (tag, value)를 반환한다. 값 타입이 SHORT(2바이트)냐 LONG(4바이트)이냐에
# 따라 12바이트짜리 값 필드 안에서 읽는 폭이 다르다.
def _tiff_ifd_entry(
    contents: bytes, entry_start: int, endian: str
) -> tuple[int, int] | None:
    tag = int.from_bytes(contents[entry_start : entry_start + 2], endian)
    if tag not in (_TIFF_WIDTH_TAG, _TIFF_HEIGHT_TAG):
        return None
    value_type = int.from_bytes(contents[entry_start + 2 : entry_start + 4], endian)
    value_field = contents[entry_start + 8 : entry_start + 12]
    if value_type == _TIFF_SHORT_TYPE:
        return tag, int.from_bytes(value_field[0:2], endian)
    if value_type == _TIFF_LONG_TYPE:
        return tag, int.from_bytes(value_field, endian)
    return None


# TIFF의 첫 IFD(Image File Directory)를 순회하며 크기 태그를 찾는다.
def _tiff_dimensions(contents: bytes) -> tuple[int, int] | None:
    endian = _tiff_byte_order(contents)
    if endian is None:
        return None
    ifd_offset = int.from_bytes(contents[4:8], endian)
    if ifd_offset + 2 > len(contents):
        return None
    entry_count = int.from_bytes(contents[ifd_offset : ifd_offset + 2], endian)
    entries_start = ifd_offset + 2
    entries_end = entries_start + entry_count * _TIFF_IFD_ENTRY_BYTES
    if entries_end > len(contents):
        return None
    dimensions: dict[int, int] = {}
    for index in range(entry_count):
        entry_start = entries_start + index * _TIFF_IFD_ENTRY_BYTES
        entry = _tiff_ifd_entry(contents, entry_start, endian)
        if entry is not None:
            dimensions[entry[0]] = entry[1]
    width = dimensions.get(_TIFF_WIDTH_TAG)
    height = dimensions.get(_TIFF_HEIGHT_TAG)
    if width is None or height is None or width <= 0 or height <= 0:
        return None
    return width, height


def _original_image_dimensions(
    asset_root: Path, image_id: str
) -> tuple[int, int] | None:
    """Best-effort original-image pixel size, read from `input_manifest.json`."""
    path = asset_root / "manifests" / "input_manifest.json"
    try:
        decoded = parse_json_object(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ContractValidationError):
        return None
    raw_images = decoded.get("images")
    if not isinstance(raw_images, list):
        return None
    for raw in raw_images:
        if not isinstance(raw, dict) or raw.get("image_id") != image_id:
            continue
        return _manifest_entry_dimensions(raw, asset_root)
    return None


# _original_image_dimensions에서 image_id가 일치하는 항목 하나를 찾은 뒤
# 호출한다. 원본 이미지 경로 해석부터 바이트 읽기·크기 파싱까지 담당한다.
def _manifest_entry_dimensions(
    raw: JsonValue, asset_root: Path
) -> tuple[int, int] | None:
    if not isinstance(raw, dict):
        return None
    raw_path = raw.get("run_root_asset_path")
    if not isinstance(raw_path, str):
        return None
    original_image = _contained_asset(Path(raw_path), asset_root)
    if original_image is None:
        return None
    try:
        contents = original_image.read_bytes()
    except OSError:
        return None
    return _decoded_dimensions(contents)


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
    original_dimensions = _original_image_dimensions(asset_root, raw.image_id)
    return JoinedRefinementAssets(
        object_id,
        roi_image,
        object_mask,
        *dimensions,
        view,
        *(original_dimensions or (None, None)),
    )
