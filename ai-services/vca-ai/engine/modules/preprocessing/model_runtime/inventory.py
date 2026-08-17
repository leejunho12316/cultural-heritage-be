"""Model inventory loading for local preprocessing caches."""

# pyright: reportAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from modules.shared import ContractValidationError, resolve_model_path

if TYPE_CHECKING:
    from modules.preprocessing.model_runtime.options import RealPreprocessingOptions


REQUIRED_MODEL_KEYS: Final = frozenset(
    {
        "owlv2_sam2.detector",
        "sam2.segmenter",
    }
)


@dataclass(frozen=True, slots=True)
class ModelInventoryEntry:
    """One validated model cache entry used by real preprocessing."""

    key: str
    repo_id: str
    revision: str
    local_dir: Path


def _string_field(item: dict[str, str], field_name: str) -> str:
    value = item.get(field_name)
    if value is None or not value.strip():
        field = f"model_inventory.models.{field_name}"
        reason = "must be a non-empty string"
        raise ContractValidationError(field, reason)
    return value


def inventory_path(options: RealPreprocessingOptions) -> Path:
    """Return the model inventory file path for the selected cache root."""
    return options.model_cache_root / "inventory" / "model_inventory.json"


def load_model_inventory(
    options: RealPreprocessingOptions,
) -> dict[str, ModelInventoryEntry]:
    """Load and validate all required model inventory entries."""
    try:
        parsed: object = json.loads(inventory_path(options).read_text())
    except json.JSONDecodeError as error:
        field = "model_inventory"
        reason = f"malformed JSON: {error.msg}"
        raise ContractValidationError(field, reason) from error
    if not isinstance(parsed, dict):
        field = "model_inventory"
        reason = "must be a JSON object"
        raise ContractValidationError(field, reason)
    raw_models = parsed.get("models")
    if not isinstance(raw_models, list):
        field = "model_inventory.models"
        reason = "must be a list"
        raise ContractValidationError(field, reason)
    entries: dict[str, ModelInventoryEntry] = {}
    for raw_item in raw_models:
        if not isinstance(raw_item, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in raw_item.items()
        ):
            field = "model_inventory.models"
            reason = "must contain string-valued objects"
            raise ContractValidationError(field, reason)
        item = dict(raw_item)
        key = _string_field(item, "key")
        entry = ModelInventoryEntry(
            key=key,
            repo_id=_string_field(item, "repo_id"),
            revision=_string_field(item, "revision"),
            local_dir=resolve_model_path(
                key, Path(_string_field(item, "local_dir")), options.model_cache_root
            ),
        )
        entries[entry.key] = entry
    missing = REQUIRED_MODEL_KEYS.difference(entries)
    if missing:
        field = "model_inventory.models"
        reason = f"missing required keys: {','.join(sorted(missing))}"
        raise ContractValidationError(field, reason)
    return entries


def model_paths(options: RealPreprocessingOptions) -> dict[str, Path]:
    """Return validated model keys and local directories."""
    return {
        key: entry.local_dir for key, entry in load_model_inventory(options).items()
    }


def validate_model_cache(entries: dict[str, ModelInventoryEntry]) -> None:
    """Reject missing local model cache directories before heavy model loading."""
    for key, entry in entries.items():
        if not entry.local_dir.exists():
            field = f"model_inventory.models.{key}.local_dir"
            reason = "local cache path does not exist"
            raise ContractValidationError(field, reason)
