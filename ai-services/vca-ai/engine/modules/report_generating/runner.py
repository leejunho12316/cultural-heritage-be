"""Standalone CLI for trace and final evidence report generation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING

from modules.report_generating.final import generate_final_report
from modules.report_generating.io import read_request, read_trace_source, write_json
from modules.report_generating.trace import generate_trace_report
from modules.report_generating.verification import (
    verify_final_report,
    verify_trace_report,
)
from modules.shared import (
    ContractValidationError,
    ExitCode,
    PathSafetyError,
    ensure_no_symlink_leaf,
    ensure_safe_run_root,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from modules.report_generating.models import JsonObject, ReportGeneratingRequest


class _CliNamespace(argparse.Namespace):
    input_json: Path
    output_json: Path

    def __init__(self) -> None:
        super().__init__()
        self.input_json = Path()
        self.output_json = Path()


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the standalone JSON request/response report generator."""
    parsed = _CliNamespace()
    _ = _parser().parse_args(arguments, namespace=parsed)
    try:
        output_path = ensure_no_symlink_leaf(
            parsed.output_json, "report generation output is a symlink"
        )
        result = run_report_generation(read_request(parsed.input_json))
        write_json(output_path, result)
    except (
        ContractValidationError,
        FileNotFoundError,
        json.JSONDecodeError,
        PathSafetyError,
    ):
        return int(ExitCode.INCOMPLETE_OR_FAILURE)
    return int(ExitCode.OK) if result["verification_status"] == "pass" else int(
        ExitCode.INCOMPLETE_OR_FAILURE
    )


def run_report_generation(request: ReportGeneratingRequest) -> JsonObject:
    """Generate both report layers and return their verification outcome."""
    run_root = ensure_safe_run_root(request.workspace_root, request.run_root)
    source_path = request.trace_source_path.resolve()
    if not source_path.is_relative_to(request.workspace_root.resolve()):
        raise PathSafetyError(
            str(request.trace_source_path), "trace source is outside workspace"
        )
    trace_root = generate_trace_report(run_root, read_trace_source(source_path))
    trace_receipt = verify_trace_report(run_root)
    if not trace_receipt.passed:
        return _result(trace_root, None, "failed")
    final_root = generate_final_report(run_root)
    final_receipt = verify_final_report(run_root)
    status = "pass" if final_receipt.passed else "fail"
    return _result(trace_root, final_root, status)


def _result(trace_root: Path, final_root: Path | None, status: str) -> JsonObject:
    return {
        "schema": "report_generating_result_v1",
        "verification_status": status,
        "trace_root": str(trace_root),
        "final_root": str(final_root) if final_root is not None else None,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--input-json", required=True, type=Path)
    _ = parser.add_argument("--output-json", required=True, type=Path)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
