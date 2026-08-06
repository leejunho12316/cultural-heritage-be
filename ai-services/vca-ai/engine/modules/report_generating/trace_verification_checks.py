"""Trace metadata and page-level verification checks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from modules.report_generating.verification_shared import (
    boolean,
    contained,
    contains_all,
    first_error,
    integer,
    json_list,
    json_object,
    json_object_value,
    links,
    links_resolve,
    mismatch,
    sha256,
    string,
)

if TYPE_CHECKING:
    from pathlib import Path

    from modules.report_generating.models import JsonObject, JsonValue

TRACE_VISIBLE_FIELDS = (
    "Trace Report",
    "Evidence Trace",
    "Candidate Evidence",
    "Budget Status",
)


def trace_metadata_error(
    run_root: Path,
    metadata: JsonObject,
    schema: str,
) -> str | None:
    """Return the first trace metadata contract failure."""
    candidates = json_list(metadata, "candidates")
    return first_error(
        (
            mismatch(
                string(metadata, "schema"), schema, "trace metadata schema mismatch"
            ),
            mismatch(
                string(metadata, "run_root"),
                str(run_root.resolve()),
                "trace metadata run-root mismatch",
            ),
            _claim_audit_error(
                json_object(metadata, "no_fake_claim_audit"), len(candidates)
            ),
        )
    )


def trace_html_error(
    report_root: Path,
    index_path: Path,
    metadata: JsonObject,
) -> str | None:
    """Return the first trace HTML/link/digest failure."""
    index_text = index_path.read_text(encoding="utf-8")
    candidates = json_list(metadata, "candidates")
    expected_digests = json_object(metadata, "digests")
    page_digests = json_object(expected_digests, "candidate_pages_sha256")
    return first_error(
        (
            _missing_trace_fields_error(index_text),
            mismatch(
                string(expected_digests, "index_html_sha256"),
                sha256(index_path),
                "trace index digest mismatch",
            ),
            _candidate_page_count_error(page_digests, candidates),
            _candidate_pages_error(report_root, index_text, page_digests, candidates),
            "trace index has broken internal links"
            if not links_resolve(report_root, index_path)
            else None,
        )
    )


def trace_digest_contract_error(
    receipt: JsonObject,
    digests: JsonObject,
) -> str | None:
    """Return whether receipt digests differ from current trace files."""
    return (
        "trace verification receipt digest mismatch"
        if json_object(receipt, "digests") != digests
        else None
    )


def _claim_audit_error(audit: JsonObject, candidate_count: int) -> str | None:
    return first_error(
        (
            mismatch(string(audit, "status"), "pass", "trace claim audit is failed"),
            _claim_count_error(audit, candidate_count),
            "trace claim audit fabricated count is nonzero"
            if integer(audit, "fabricated_candidate_count") != 0
            else None,
            "trace claim audit runner was not invoked"
            if not boolean(audit, "runner_invoked")
            else None,
        )
    )


def _claim_count_error(audit: JsonObject, candidate_count: int) -> str | None:
    return (
        "trace claim audit count mismatch"
        if integer(audit, "claimed_candidate_count") != candidate_count
        else None
    )


def _missing_trace_fields_error(content: str) -> str | None:
    return (
        "trace index lacks required visible fields"
        if not contains_all(content, TRACE_VISIBLE_FIELDS)
        else None
    )


def _candidate_page_count_error(
    page_digests: JsonObject,
    candidates: list[JsonValue],
) -> str | None:
    return (
        "trace candidate page digest count mismatch"
        if len(page_digests) != len(candidates)
        else None
    )


def _candidate_pages_error(
    report_root: Path,
    index_text: str,
    page_digests: JsonObject,
    candidates: list[JsonValue],
) -> str | None:
    for value in candidates:
        error = _candidate_page_error(
            report_root, index_text, page_digests, json_object_value(value)
        )
        if error is not None:
            return error
    return None


def _candidate_page_error(
    report_root: Path,
    index_text: str,
    page_digests: JsonObject,
    candidate: JsonObject,
) -> str | None:
    candidate_id = string(candidate, "candidate_id")
    page_path = report_root / "candidates" / candidate_id / "index.html"
    href = f"candidates/{candidate_id}/index.html"
    return first_error(
        (
            _candidate_page_path_error(report_root, page_path),
            "trace candidate page link mismatch"
            if href not in links(index_text) or not page_path.is_file()
            else None,
            mismatch(
                string(page_digests, candidate_id),
                sha256(page_path) if page_path.is_file() else "",
                "trace candidate page digest mismatch",
            ),
            "trace candidate page has broken internal links"
            if page_path.is_file() and not links_resolve(report_root, page_path)
            else None,
        )
    )


def _candidate_page_path_error(report_root: Path, page_path: Path) -> str | None:
    if page_path.is_symlink():
        return "trace candidate page is a symlink"
    if not contained(report_root, page_path):
        return "trace candidate page escapes report root"
    return None
