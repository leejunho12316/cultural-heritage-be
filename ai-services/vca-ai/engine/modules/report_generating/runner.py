"""Standalone CLI for trace and final evidence report generation."""

from __future__ import annotations

import argparse
import json
import sys
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


# 독립 실행 report_generating CLI 진입점(__main__에서 호출).
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
    ) as error:
        print(  # noqa: T201
            f"report_generating: failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return int(ExitCode.INCOMPLETE_OR_FAILURE)
    return int(ExitCode.OK) if result["verification_status"] == "pass" else int(
        ExitCode.INCOMPLETE_OR_FAILURE
    )


# main과 startup_runner.py의 run_report_generating_stage 양쪽에서 호출되는
# 핵심 오케스트레이션 함수. trace 리포트를 만들고 검증한 뒤, 통과한 경우에만
# 최종 한국어 리포트까지 생성한다.
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


# run_report_generation에서 반환 JSON payload를 만드는 헬퍼.
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
