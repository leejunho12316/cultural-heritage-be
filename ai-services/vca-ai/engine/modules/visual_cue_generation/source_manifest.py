"""Preprocessing input-manifest adapter for Qwen source assets."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import NoReturn

from modules.rag.qwen.qwen_bridge_json import parse_json_object
from modules.rough_masking import AssetReference
from modules.shared import ContractValidationError, ImageId

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def source_assets_by_image(
    input_manifest_path: Path,
    asset_root: Path,
) -> dict[ImageId, AssetReference]:
    """Return source image assets keyed by image ID."""
    payload = _json_object(input_manifest_path.read_text(encoding="utf-8"))
    images = payload.get("images")
    if not isinstance(images, list):
        _raise_contract("images", "must be a list")
    assets: dict[ImageId, AssetReference] = {}
    for raw_image in images:
        if not isinstance(raw_image, dict):
            _raise_contract("images", "items must be objects")
        image_id = _string(raw_image, "image_id")
        assets[ImageId(image_id)] = _asset(raw_image, asset_root)
    return assets


# run_root_asset_path가 asset_root 내부의 실제 파일을 가리키는지 확인하고,
# 매니페스트에 기록된 file_sha256과 실제 해시가 일치하는지 검증한다.
def _asset(raw_image: dict[str, JsonValue], asset_root: Path) -> AssetReference:
    raw_path = _string(raw_image, "run_root_asset_path")
    path = Path(raw_path)
    if not path.is_absolute():
        _raise_contract("run_root_asset_path", "must be absolute")
    resolved_root = asset_root.resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(resolved_root):
        _raise_contract("run_root_asset_path", "must stay inside asset_root")
    if not resolved_path.is_file():
        _raise_contract("run_root_asset_path", "source asset missing")
    media_type = _string(raw_image, "mime_type")
    expected_hash = _string(raw_image, "file_sha256")
    actual_hash = sha256(resolved_path.read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        _raise_contract("file_sha256", "source asset hash mismatch")
    relative_path = resolved_path.relative_to(resolved_root).as_posix()
    return AssetReference(relative_path, actual_hash, media_type)


def _json_object(text: str) -> dict[str, JsonValue]:
    return parse_json_object(text)


def _string(payload: dict[str, JsonValue], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        _raise_contract(field, "must be a non-blank string")
    return value


def _raise_contract(field: str, reason: str) -> NoReturn:
    raise ContractValidationError(field, reason)
