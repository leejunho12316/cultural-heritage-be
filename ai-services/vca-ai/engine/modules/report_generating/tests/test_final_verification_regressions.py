from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from modules.report_generating.io import read_json_object, write_json
from modules.report_generating.tests.test_support import generate_reports
from modules.report_generating.verification import verify_final_report

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("run_root", "stale-run-root", "final metadata run-root mismatch"),
        (
            "trace_index_path",
            "../report/stale-index.html",
            "final metadata trace index path mismatch",
        ),
        (
            "trace_receipt_sha256",
            "0" * 64,
            "final metadata trace receipt digest mismatch",
        ),
        ("final_success", False, "final metadata final-success mismatch"),
    ],
)
def test_final_verifier_rejects_tampered_provenance(
    tmp_path: Path, field: str, value: str | bool, reason: str
) -> None:
    # Given: final metadata produced from a passing verified trace report.
    run_root = generate_reports(tmp_path, final_success=True)
    metadata_path = run_root / "final_report" / "metadata.json"
    metadata = read_json_object(metadata_path)
    metadata[field] = value
    write_json(metadata_path, metadata)

    # When: static final verification receives stale provenance.
    receipt = verify_final_report(run_root)

    # Then: each trace-derived field blocks publication independently.
    assert receipt.passed is False
    assert receipt.reason == reason
