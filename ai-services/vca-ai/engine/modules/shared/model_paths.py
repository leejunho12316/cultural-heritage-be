"""Project-local resolution for local model directories."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Final

from modules.shared.errors import ContractValidationError

MODELS_FILE_NAME: Final = ".models"
MODEL_PATH_ENVIRONMENT_PREFIX: Final = "VCA_MODEL_"
MODEL_PATH_ENVIRONMENT_SUFFIX: Final = "_PATH"
_MODEL_KEY_COMPONENT = re.compile(r"[^A-Za-z0-9]+")


def model_path_environment_variable(model_key: str) -> str:
    """Return the process and .models key used to override one model path."""
    component = _MODEL_KEY_COMPONENT.sub("_", model_key).strip("_").upper()
    if not component:
        field = "model_key"
        reason = "must contain an alphanumeric character"
        raise ContractValidationError(field, reason)
    return f"{MODEL_PATH_ENVIRONMENT_PREFIX}{component}{MODEL_PATH_ENVIRONMENT_SUFFIX}"


def parse_model_paths(models_file: Path) -> dict[str, str]:
    """Load non-empty KEY=VALUE local model path overrides from .models."""
    if not models_file.exists():
        return {}
    paths: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        models_file.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, configured_path = raw_line.partition("=")
        environment_variable = key.strip()
        if not separator or not environment_variable:
            field = "models_file"
            reason = f"line {line_number} must use KEY=VALUE form"
            raise ContractValidationError(
                field, reason
            )
        path_value = configured_path.strip()
        if not path_value:
            raise ContractValidationError(
                environment_variable, "model path must not be empty"
            )
        paths[environment_variable] = path_value
    return paths


def resolve_model_path(
    model_key: str, inventory_local_dir: Path, model_cache_root: Path
) -> Path:
    """Resolve one local model path from process, .models, then inventory."""
    workspace_root = model_cache_root.parent
    environment_variable = model_path_environment_variable(model_key)
    file_paths = parse_model_paths(workspace_root / MODELS_FILE_NAME)
    process_path = os.environ.get(environment_variable)
    if process_path is not None:
        return _resolve_configured_path(
            process_path, environment_variable, workspace_root
        )
    configured_path = file_paths.get(environment_variable)
    if configured_path is not None:
        return _resolve_configured_path(
            configured_path, environment_variable, workspace_root
        )
    return _resolve_workspace_path(inventory_local_dir, workspace_root)


def _resolve_configured_path(
    configured_path: str, environment_variable: str, workspace_root: Path
) -> Path:
    if not configured_path.strip():
        reason = "model path must not be empty"
        raise ContractValidationError(environment_variable, reason)
    return _resolve_workspace_path(Path(configured_path), workspace_root)


def _resolve_workspace_path(path: Path, workspace_root: Path) -> Path:
    return path if path.is_absolute() else workspace_root / path
