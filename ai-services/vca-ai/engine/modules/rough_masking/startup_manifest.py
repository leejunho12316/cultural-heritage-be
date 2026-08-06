"""Preprocessing manifest parsing for rough-mask startup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from modules.preprocessing import BoundingBox, ScaleConfidence, ScaleMetadata
from modules.preprocessing.contracts.records import (
    MaterializedAssetRecord,
    ObjectAssetRecord,
)
from modules.preprocessing.model_runtime.inventory import ModelInventoryEntry
from modules.rag.qwen.qwen_bridge_json import JsonObject, JsonValue, parse_json_object
from modules.shared import (
    ContractValidationError,
    parse_detector_lane,
    resolve_model_path,
)

MANIFEST_SCHEMA_VERSION: Final = "vca-real-preprocessing-v2"
BBOX_COORDINATE_COUNT: Final = 4


@dataclass(frozen=True, slots=True)
class StartupManifest:
    """Typed subset of the preprocessing manifest used by rough masking."""

    detector_lane_status: str
    model_invocations: int
    sam2_calls: int
    object_count: int
    objects: tuple[ObjectAssetRecord, ...]


def load_startup_manifest(preprocessing_root: Path) -> StartupManifest:
    """Load the current real-preprocessing manifest contract."""
    manifest_path = (
        preprocessing_root / "manifests" / "real_preprocessing_manifest.json"
    )
    try:
        decoded = parse_json_object(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        field = "preprocessing_manifest"
        reason = "file missing"
        raise _invalid(field, reason) from error
    if _string(decoded, "schema_version") != MANIFEST_SCHEMA_VERSION:
        field = "preprocessing_manifest.schema_version"
        reason = "unsupported"
        raise _invalid(field, reason)
    raw_objects = _list(decoded, "objects")
    objects = tuple(_object_record(item) for item in raw_objects)
    object_count = _integer(decoded, "object_count")
    if object_count != len(objects):
        field = "preprocessing_manifest.object_count"
        reason = "mismatch"
        raise _invalid(field, reason)
    return StartupManifest(
        _string(decoded, "detector_lane_status"),
        _integer(decoded, "model_invocations"),
        _integer(decoded, "sam2_calls"),
        object_count,
        objects,
    )


def load_model_entries(model_cache_root: Path) -> dict[str, ModelInventoryEntry]:
    """Load model inventory entries required by local rough-mask runners."""
    inventory_path = model_cache_root / "inventory" / "model_inventory.json"
    try:
        decoded = parse_json_object(inventory_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        field = "model_inventory"
        reason = "file missing"
        raise _invalid(field, reason) from error
    raw_models = _list(decoded, "models")
    entries: dict[str, ModelInventoryEntry] = {}
    for raw_entry in raw_models:
        entry = _json_object(raw_entry, "model_inventory.models")
        key = _string(entry, "key")
        entries[key] = ModelInventoryEntry(
            key,
            _string(entry, "repo_id"),
            _string(entry, "revision"),
            resolve_model_path(
                key, Path(_string(entry, "local_dir")), model_cache_root
            ),
        )
    return entries


def _object_record(raw: JsonValue) -> ObjectAssetRecord:
    item = _json_object(raw, "preprocessing_manifest.objects")
    return ObjectAssetRecord(
        candidate_id=_string(item, "candidate_id"),
        object_id=_string(item, "object_id"),
        image_id=_string(item, "image_id"),
        lane=parse_detector_lane(_string(item, "lane")),
        accepted=_boolean(item, "accepted"),
        diagnostics=tuple(_string_values(item, "diagnostics")),
        bbox_xyxy=_bbox_xyxy(item),
        score=_number(item, "score"),
        sam2_score=_number(item, "sam2_score"),
        prompt_pack_id=_string(item, "prompt_pack_id"),
        prompt_role=_string(item, "prompt_role"),
        prompt_text=_string(item, "prompt_text"),
        generated_prompt_id=_string(item, "generated_prompt_id"),
        source_terms=tuple(_string_values(item, "source_terms")),
        detector_model_id=_string(item, "detector_model_id"),
        detector_model_revision=_string(item, "detector_model_revision"),
        sam2_model_id=_string(item, "sam2_model_id"),
        sam2_model_revision=_string(item, "sam2_model_revision"),
        device=_string(item, "device"),
        source_image_sha256=_string(item, "source_image_sha256"),
        detector_input_sha256=_string(item, "detector_input_sha256"),
        scale_metadata=_scale_metadata(
            _json_object(item.get("scale_metadata"), "scale_metadata")
        ),
        scale_removal_applied=_boolean(item, "scale_removal_applied"),
        mask=_asset(item, "mask"),
        bbox_crop=_asset(item, "bbox_crop"),
        alpha_cutout=_asset(item, "alpha_cutout"),
        detection_overlay=_asset(item, "detection_overlay"),
        tile=_asset(item, "tile"),
        tiles=tuple(
            _asset(_json_object(tile, "tiles"), "") for tile in _list(item, "tiles")
        ),
    )


def _scale_metadata(raw: JsonObject) -> ScaleMetadata:
    marker_bbox = raw.get("scale_marker_bbox")
    return ScaleMetadata(
        scale_marker_detected=_boolean(raw, "scale_marker_detected"),
        scale_marker_bbox=(
            None
            if marker_bbox is None
            else _bounding_box(_json_object(marker_bbox, "scale_marker_bbox"))
        ),
        scale_marker_width_px=_optional_number(raw, "scale_marker_width_px"),
        scale_unit_px=_optional_number(raw, "scale_unit_px"),
        scale_unit_source=_optional_string(raw, "scale_unit_source"),
        scale_confidence=ScaleConfidence(_string(raw, "scale_confidence")),
        confidence_reasons=tuple(_string_values(raw, "confidence_reasons")),
        fallback_reason=_optional_string(raw, "fallback_reason"),
        scale_unit_label=_optional_string(raw, "scale_unit_label"),
        scale_unit_value=_optional_number(raw, "scale_unit_value"),
        scale_marker_orientation=_optional_string(raw, "scale_marker_orientation"),
        scale_recognition_method=_string(raw, "scale_recognition_method"),
    )


def _bounding_box(raw: JsonObject) -> BoundingBox:
    return BoundingBox(
        _number(raw, "left"),
        _number(raw, "top"),
        _number(raw, "width"),
        _number(raw, "height"),
    )


def _asset(item: JsonObject, field_name: str) -> MaterializedAssetRecord:
    raw = item if not field_name else _json_object(item.get(field_name), field_name)
    return MaterializedAssetRecord(
        path=_string(raw, "path"),
        sha256=_string(raw, "sha256"),
        media_type=_string(raw, "media_type"),
    )


def _bbox_xyxy(item: JsonObject) -> tuple[float, float, float, float]:
    values = tuple(
        _number_value(value, "bbox_xyxy") for value in _list(item, "bbox_xyxy")
    )
    if len(values) != BBOX_COORDINATE_COUNT:
        field = "bbox_xyxy"
        reason = "must contain four coordinates"
        raise _invalid(field, reason)
    return values


def _invalid(field: str, reason: str) -> ContractValidationError:
    return ContractValidationError(field, reason)


def _json_object(raw: JsonValue | None, field_name: str) -> JsonObject:
    if not isinstance(raw, dict):
        raise _invalid(field_name, "must be an object")
    return raw


def _list(item: JsonObject, field_name: str) -> list[JsonValue]:
    value = item.get(field_name)
    if not isinstance(value, list):
        raise _invalid(field_name, "must be a list")
    return value


def _string(item: JsonObject, field_name: str) -> str:
    value = item.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise _invalid(field_name, "must be a non-empty string")
    return value


def _optional_string(item: JsonObject, field_name: str) -> str | None:
    value = item.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _invalid(field_name, "must be a non-empty string")
    return value


def _string_values(item: JsonObject, field_name: str) -> tuple[str, ...]:
    values = _list(item, field_name)
    strings: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise _invalid(field_name, "must contain strings")
        strings.append(value)
    return tuple(strings)


def _boolean(item: JsonObject, field_name: str) -> bool:
    value = item.get(field_name)
    if not isinstance(value, bool):
        raise _invalid(field_name, "must be a boolean")
    return value


def _integer(item: JsonObject, field_name: str) -> int:
    value = item.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid(field_name, "must be an integer")
    return value


def _number(item: JsonObject, field_name: str) -> float:
    return _number_value(item.get(field_name), field_name)


def _optional_number(item: JsonObject, field_name: str) -> float | None:
    value = item.get(field_name)
    return None if value is None else _number_value(value, field_name)


def _number_value(value: JsonValue | None, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _invalid(field_name, "must be numeric")
    return float(value)
