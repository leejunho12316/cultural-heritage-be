from __future__ import annotations

from typing import TYPE_CHECKING

from modules.report_generating.browser_qa import main as browser_qa_main
from modules.report_generating.io import read_json_object
from modules.report_generating.tests.test_support import generate_reports

if TYPE_CHECKING:
    from pathlib import Path


def test_browser_qa_receipt_identifies_static_surface_contract(tmp_path: Path) -> None:
    # Given: a passing trace and final report with desktop and mobile viewports.
    run_root = generate_reports(tmp_path, final_success=True)
    out_dir = tmp_path / "browser-qa"

    # When: the deterministic browser-surface command records its checks.
    exit_code = browser_qa_main(
        (
            "--run-root",
            str(run_root),
            "--viewports",
            "1440x1000",
            "390x844",
            "--out-dir",
            str(out_dir),
        )
    )

    # Then: the receipt reports static verification instead of browser execution.
    assert exit_code == 0
    receipt = read_json_object(out_dir / "receipt.json")
    assert receipt["status"] == "pass"
    assert receipt["browser_execution"] == "not_run"
    assert receipt["checked_viewports"] == ["1440x1000", "390x844"]
    assert receipt["surface_contract"] == {
        "final_report": "static_verification",
        "requested_viewports": "recorded_not_rendered",
        "trace_report": "static_verification",
    }


def test_browser_qa_receipt_preserves_static_failure_status(tmp_path: Path) -> None:
    # Given: a run root without either report surface.
    run_root = tmp_path / "run"
    out_dir = tmp_path / "browser-qa"

    # When: static browser-surface QA evaluates the incomplete run.
    exit_code = browser_qa_main(
        (
            "--run-root",
            str(run_root),
            "--viewports",
            "390x844",
            "--out-dir",
            str(out_dir),
        )
    )

    # Then: it keeps the existing nonzero failure signal and receipt status.
    assert exit_code == 2
    receipt = read_json_object(out_dir / "receipt.json")
    assert receipt["status"] == "fail"


def test_browser_qa_rejects_symlinked_output_directory(tmp_path: Path) -> None:
    # Given: browser QA output is redirected through a directory symlink.
    run_root = generate_reports(tmp_path, final_success=True)
    external_directory = tmp_path / "external-browser-qa"
    external_directory.mkdir()
    out_dir = tmp_path / "browser-qa"
    out_dir.symlink_to(external_directory, target_is_directory=True)

    # When: the static browser-surface command tries to write its receipt.
    exit_code = browser_qa_main(
        (
            "--run-root",
            str(run_root),
            "--viewports",
            "390x844",
            "--out-dir",
            str(out_dir),
        )
    )

    # Then: it fails closed and writes nothing outside the requested directory.
    assert exit_code == 2
    assert tuple(external_directory.iterdir()) == ()
