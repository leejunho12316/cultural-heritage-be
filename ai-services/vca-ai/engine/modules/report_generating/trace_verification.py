"""Trace report static verification."""

from __future__ import annotations

from typing import TYPE_CHECKING

from modules.report_generating.io import read_json_object, write_json
from modules.report_generating.models import (
    TRACE_METADATA_SCHEMA,
    TRACE_VERIFICATION_SCHEMA,
    JsonObject,
    VerificationReceipt,
)
from modules.report_generating.trace_verification_checks import (
    trace_digest_contract_error,
    trace_html_error,
    trace_metadata_error,
)
from modules.report_generating.verification_shared import (
    contained,
    first_error,
    mismatch,
    receipt_payload,
    sha256,
    string,
)
from modules.shared import ensure_contained_write_path

if TYPE_CHECKING:
    from pathlib import Path

def verify_trace_report(run_root: Path) -> VerificationReceipt:
    """Verify trace artifacts and persist a machine-readable receipt."""
    report_root = run_root / "report"
    reason = _trace_static_error(run_root, report_root)
    receipt = VerificationReceipt(
        TRACE_VERIFICATION_SCHEMA,
        "pass" if reason is None else "fail",
        str(run_root.resolve()),
        str(report_root.resolve()),
        reason,
        trace_digests(report_root),
    )
    receipt_path = ensure_contained_write_path(
        run_root,
        report_root / "verification" / "receipt.json",
        "trace verification receipt path is unsafe",
    )
    write_json(receipt_path, receipt_payload(receipt))
    return receipt


def trace_receipt_integrity_error(run_root: Path) -> str | None:
    """Return a refusal reason unless trace evidence matches its receipt."""
    report_root = run_root / "report"
    receipt_path = report_root / "verification" / "receipt.json"
    receipt = _read_trace_receipt(receipt_path)
    if isinstance(receipt, str):
        return receipt
    return first_error(
        (
            _trace_static_error(run_root, report_root),
            _trace_receipt_contract_error(run_root, report_root, receipt),
        )
    )


def _trace_static_error(run_root: Path, report_root: Path) -> str | None:
    index_path = report_root / "index.html"
    metadata_path = report_root / "metadata.json"
    path_error = _trace_path_error(run_root, report_root, index_path, metadata_path)
    if path_error is not None:
        return path_error
    metadata = _read_trace_metadata(metadata_path)
    if isinstance(metadata, str):
        return metadata
    return first_error(
        (
            trace_metadata_error(run_root, metadata, TRACE_METADATA_SCHEMA),
            trace_html_error(report_root, index_path, metadata),
        )
    )


def trace_digests(report_root: Path) -> JsonObject:
    """Return trace artifact digests used by metadata and receipts."""
    metadata_path = report_root / "metadata.json"
    index_path = report_root / "index.html"
    if (
        not metadata_path.is_file()
        or not index_path.is_file()
        or metadata_path.is_symlink()
        or index_path.is_symlink()
    ):
        return {}
    candidate_digests: JsonObject = {}
    candidates_root = report_root / "candidates"
    if candidates_root.is_dir():
        for path in sorted(candidates_root.glob("*/index.html")):
            if path.is_symlink() or not contained(report_root, path):
                continue
            candidate_digests[path.parent.name] = sha256(path)
    return {
        "metadata_sha256": sha256(metadata_path),
        "index_html_sha256": sha256(index_path),
        "candidate_pages_sha256": candidate_digests,
    }


def _read_trace_receipt(path: Path) -> JsonObject | str:
    if not path.is_file():
        return "trace verification receipt is missing"
    try:
        return read_json_object(path)
    except (OSError, ValueError):
        return "trace verification receipt is unreadable"


def _read_trace_metadata(path: Path) -> JsonObject | str:
    try:
        return read_json_object(path)
    except (OSError, ValueError):
        return "trace metadata is unreadable"


def _trace_receipt_contract_error(
    run_root: Path,
    report_root: Path,
    receipt: JsonObject,
) -> str | None:
    return first_error(
        (
            mismatch(
                string(receipt, "schema"),
                TRACE_VERIFICATION_SCHEMA,
                "trace verification receipt schema mismatch",
            ),
            mismatch(
                string(receipt, "verification_status"),
                "pass",
                "trace verification receipt is failed",
            ),
            mismatch(
                string(receipt, "run_root"),
                str(run_root.resolve()),
                "trace verification receipt run-root mismatch",
            ),
            mismatch(
                string(receipt, "artifact_root"),
                str(report_root.resolve()),
                "trace verification receipt artifact path mismatch",
            ),
            trace_digest_contract_error(receipt, trace_digests(report_root)),
        )
    )


def _trace_path_error(
    run_root: Path,
    report_root: Path,
    index_path: Path,
    metadata_path: Path,
) -> str | None:
    return first_error(
        (
            "trace report escapes run root"
            if not contained(run_root, report_root) or report_root.is_symlink()
            else None,
            "trace report leaf is a symlink"
            if index_path.is_symlink() or metadata_path.is_symlink()
            else None,
            "trace report requires index and metadata"
            if not index_path.is_file() or not metadata_path.is_file()
            else None,
        )
    )
