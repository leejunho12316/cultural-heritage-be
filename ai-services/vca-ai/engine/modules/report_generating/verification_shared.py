"""Shared helpers for report verification receipts."""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from modules.report_generating.models import (
        JsonObject,
        JsonValue,
        VerificationReceipt,
    )

HREF_PATTERN = re.compile(r'href="([^"]+)"')


def receipt_payload(receipt: VerificationReceipt) -> JsonObject:
    """Serialize a typed verification receipt."""
    return {
        "schema": receipt.schema,
        "verification_status": receipt.verification_status,
        "run_root": receipt.run_root,
        "artifact_root": receipt.artifact_root,
        "reason": receipt.reason,
        "digests": receipt.digests,
    }


def links_resolve(root: Path, page: Path) -> bool:
    """Return whether every local href remains inside root and exists."""
    for href in links(page.read_text(encoding="utf-8")):
        if href.startswith(("http://", "https://", "#")):
            continue
        resolved = (page.parent / href).resolve()
        if not contained(root, resolved) or not resolved.is_file():
            return False
    return True


def links(content: str) -> tuple[str, ...]:
    """Extract local href values from static report HTML."""
    return tuple(HREF_PATTERN.findall(content))


def contains_all(content: str, fields: tuple[str, ...]) -> bool:
    """Return whether all required visible fields are present."""
    return all(field in content for field in fields)


def contained(root: Path, path: Path) -> bool:
    """Return whether path resolves inside root."""
    return path.resolve().is_relative_to(root.resolve())


def sha256(path: Path) -> str:
    """Return the SHA-256 digest for a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def string(payload: JsonObject, field: str) -> str:
    """Read a JSON string field or return an empty sentinel."""
    value = payload.get(field)
    return value if isinstance(value, str) else ""


def integer(payload: JsonObject, field: str) -> int:
    """Read a JSON integer field or return an invalid sentinel."""
    value = payload.get(field)
    return value if isinstance(value, int) and not isinstance(value, bool) else -1


def boolean(payload: JsonObject, field: str) -> bool:
    """Read a JSON boolean field or return false."""
    value = payload.get(field)
    return value if isinstance(value, bool) else False


def json_object(payload: JsonObject, field: str) -> JsonObject:
    """Read a nested JSON object field or return an empty sentinel."""
    return json_object_value(payload.get(field))


def json_object_value(value: JsonValue | None) -> JsonObject:
    """Coerce a JSON value to an object or return an empty sentinel."""
    return value if isinstance(value, dict) else {}


def json_list(payload: JsonObject, field: str) -> list[JsonValue]:
    """Read a JSON list field or return an empty sentinel."""
    value = payload.get(field)
    return value if isinstance(value, list) else []


def mismatch(actual: str, expected: str, reason: str) -> str | None:
    """Return reason when two contract values differ."""
    return reason if actual != expected else None


def first_error(errors: tuple[str | None, ...]) -> str | None:
    """Return the first verifier error in evaluation order."""
    for error in errors:
        if error is not None:
            return error
    return None
