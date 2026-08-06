"""Final Korean report static verification."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from modules.report_generating.io import read_json_object, write_json
from modules.report_generating.models import (
    FINAL_METADATA_SCHEMA,
    FINAL_VERIFICATION_SCHEMA,
    JsonObject,
    VerificationReceipt,
)
from modules.report_generating.trace_verification import trace_receipt_integrity_error
from modules.report_generating.verification_shared import (
    boolean,
    contained,
    contains_all,
    first_error,
    json_object,
    links,
    links_resolve,
    mismatch,
    receipt_payload,
    sha256,
    string,
)
from modules.shared import ensure_contained_write_path

if TYPE_CHECKING:
    from pathlib import Path

FINAL_VISIBLE_FIELDS = ("최종 한국어 보고서", "비진단", "Evidence Trace")
PROHIBITED_FINAL_LANGUAGE = re.compile(
    r"(?<!비)진단|치료|중증|보존|diagnosis|treatment|severity|preservation",
    re.IGNORECASE,
)
CITATION_SOURCE_BLOCK = re.compile(
    r'<li data-report-role="citation-source">.*?</li>',
    re.IGNORECASE | re.DOTALL,
)


def verify_final_report(run_root: Path) -> VerificationReceipt:
    """Verify evidence-only final artifacts and persist a receipt."""
    final_root = run_root / "final_report"
    reason = _final_static_error(run_root, final_root)
    receipt = VerificationReceipt(
        FINAL_VERIFICATION_SCHEMA,
        "pass" if reason is None else "fail",
        str(run_root.resolve()),
        str(final_root.resolve()),
        reason,
        final_digests(final_root),
    )
    receipt_path = ensure_contained_write_path(
        run_root,
        final_root / "verification" / "receipt.json",
        "final verification receipt path is unsafe",
    )
    write_json(receipt_path, receipt_payload(receipt))
    return receipt


def _final_static_error(run_root: Path, final_root: Path) -> str | None:
    index_path = final_root / "index.html"
    metadata_path = final_root / "metadata.json"
    metadata = _read_final_metadata(metadata_path)
    if isinstance(metadata, str):
        return first_error(
            (
                _final_path_error(run_root, final_root, index_path, metadata_path),
                metadata,
            )
        )
    return first_error(
        (
            _final_path_error(run_root, final_root, index_path, metadata_path),
            trace_receipt_integrity_error(run_root),
            _final_metadata_error(run_root, metadata),
            _final_html_error(run_root, index_path),
        )
    )


def final_digests(final_root: Path) -> JsonObject:
    """Return final report artifact digests used by receipts."""
    metadata_path = final_root / "metadata.json"
    index_path = final_root / "index.html"
    if not metadata_path.is_file() or not index_path.is_file():
        return {}
    return {
        "metadata_sha256": sha256(metadata_path),
        "index_html_sha256": sha256(index_path),
    }


def _read_final_metadata(path: Path) -> JsonObject | str:
    try:
        return read_json_object(path)
    except (OSError, ValueError):
        return "final metadata is unreadable"


def _final_path_error(
    run_root: Path,
    final_root: Path,
    index_path: Path,
    metadata_path: Path,
) -> str | None:
    return first_error(
        (
            "final report escapes run root"
            if not contained(run_root, final_root) or final_root.is_symlink()
            else None,
            "final report leaf is a symlink"
            if index_path.is_symlink() or metadata_path.is_symlink()
            else None,
            "final report requires index and metadata"
            if not index_path.is_file() or not metadata_path.is_file()
            else None,
        )
    )


def _final_metadata_error(run_root: Path, metadata: JsonObject) -> str | None:
    trace_metadata_path = run_root / "report" / "metadata.json"
    trace_receipt_path = run_root / "report" / "verification" / "receipt.json"
    trace_metadata = read_json_object(trace_metadata_path)
    trace_summary = json_object(trace_metadata, "run_summary")
    return first_error(
        (
            mismatch(
                string(metadata, "schema"),
                FINAL_METADATA_SCHEMA,
                "final metadata schema mismatch",
            ),
            mismatch(
                string(metadata, "trace_metadata_sha256"),
                sha256(trace_metadata_path),
                "final metadata trace digest mismatch",
            ),
            mismatch(
                string(metadata, "run_root"),
                str(run_root.resolve()),
                "final metadata run-root mismatch",
            ),
            mismatch(
                string(metadata, "trace_index_path"),
                "../report/index.html",
                "final metadata trace index path mismatch",
            ),
            mismatch(
                string(metadata, "trace_receipt_sha256"),
                sha256(trace_receipt_path),
                "final metadata trace receipt digest mismatch",
            ),
            "final metadata final-success mismatch"
            if metadata.get("final_success")
            is not boolean(trace_summary, "final_success")
            else None,
        )
    )


def _final_html_error(run_root: Path, index_path: Path) -> str | None:
    content = index_path.read_text(encoding="utf-8")
    claim_content = CITATION_SOURCE_BLOCK.sub("", content)
    return first_error(
        (
            "final index lacks required visible fields"
            if not contains_all(content, FINAL_VISIBLE_FIELDS)
            else None,
            "final index contains prohibited diagnostic language"
            if PROHIBITED_FINAL_LANGUAGE.search(claim_content) is not None
            else None,
            "final index lacks trace link"
            if "../report/index.html" not in links(content)
            else None,
            "final index has broken internal links"
            if not links_resolve(run_root, index_path)
            else None,
        )
    )
