"""Atomic writes for RAG-owned non-approval sidecars."""

import json
from collections.abc import Callable
from pathlib import Path

from modules.rag.operations.sidecars import RAG_SIDECAR_FILENAMES, JsonObject
from modules.shared import (
    ContractValidationError,
    ensure_no_symlink_leaf,
    ensure_source_document_is_not_write_target,
)

type SidecarValidator = Callable[[JsonObject], None]


def write_rag_sidecar_atomic(
    target: Path,
    payload: JsonObject,
    validator: SidecarValidator,
    source_document_root: Path | None = None,
) -> None:
    """Write one allowlisted RAG sidecar with temp validation before rename."""
    if target.name not in RAG_SIDECAR_FILENAMES:
        _raise_contract("sidecar", "must be a RAG-owned sidecar")
    if source_document_root is not None:
        _ = ensure_source_document_is_not_write_target(source_document_root, target)
    validator(payload)
    temporary_target = target.with_suffix(f"{target.suffix}.tmp")
    _ = ensure_no_symlink_leaf(target, "sidecar leaf is a symlink")
    _ = ensure_no_symlink_leaf(temporary_target, "sidecar leaf is a symlink")
    serialized_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    try:
        _ = temporary_target.write_text(serialized_payload, encoding="utf-8")
        if temporary_target.read_text(encoding="utf-8") != serialized_payload:
            _raise_contract("sidecar", "must round-trip written content")
        validator(payload)
        _ = temporary_target.replace(target)
    except (OSError, json.JSONDecodeError, ContractValidationError):
        temporary_target.unlink(missing_ok=True)
        raise


def _raise_contract(field: str, reason: str) -> None:
    raise ContractValidationError(field, reason)
