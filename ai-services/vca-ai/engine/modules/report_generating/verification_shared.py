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


# trace_verification.py/final_verification.py가 receipt.json을 기록하기 직전에
# 호출한다.
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


# trace_verification_checks.py/final_verification.py가 HTML 페이지의 내부
# 링크가 모두 root 안에서 실제 존재하는 파일을 가리키는지 검사할 때 쓴다.
# http(s)/앵커 링크는 검사 대상에서 제외한다.
def links_resolve(root: Path, page: Path) -> bool:
    """Return whether every local href remains inside root and exists."""
    for href in links(page.read_text(encoding="utf-8")):
        if href.startswith(("http://", "https://", "#")):
            continue
        resolved = (page.parent / href).resolve()
        if not contained(root, resolved) or not resolved.is_file():
            return False
    return True


# links_resolve와 trace_verification_checks.py가 호출한다.
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


# 모든 검증 함수(trace/final)가 공통으로 쓰는 패턴: 여러 개별 검사 결과 중
# None이 아닌 첫 실패 사유만 골라 반환한다.
def first_error(errors: tuple[str | None, ...]) -> str | None:
    """Return the first verifier error in evaluation order."""
    for error in errors:
        if error is not None:
            return error
    return None
