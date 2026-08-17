"""Deterministic browser-surface contract checks for static report HTML."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import TYPE_CHECKING

from modules.report_generating.io import write_json
from modules.report_generating.verification import (
    verify_final_report,
    verify_trace_report,
)
from modules.shared import ExitCode, PathSafetyError, ensure_contained_write_path

if TYPE_CHECKING:
    from collections.abc import Sequence

    from modules.report_generating.models import JsonObject, JsonValue

_VIEWPORT_PATTERN = re.compile(r"^[1-9][0-9]*x[1-9][0-9]*$")


class _CliNamespace(argparse.Namespace):
    run_root: Path
    viewports: list[str]
    out_dir: Path

    def __init__(self) -> None:
        super().__init__()
        self.run_root = Path()
        self.viewports = []
        self.out_dir = Path()


# 독립 실행 브라우저 QA CLI 진입점(__main__에서 호출). 실제 브라우저를 띄우지
# 않고 trace/final 정적 검증 결과만으로 결정적인 receipt를 만든다(브랜치명이
# 시사하듯 실제 렌더링/뷰포트 캡처는 하지 않음).
def main(arguments: Sequence[str] | None = None) -> int:
    """Emit deterministic browser-surface receipt for a report run root."""
    parsed = _CliNamespace()
    _ = _parser().parse_args(arguments, namespace=parsed)
    if not parsed.viewports or any(
        _VIEWPORT_PATTERN.fullmatch(viewport) is None for viewport in parsed.viewports
    ):
        return int(ExitCode.INCOMPLETE_OR_FAILURE)
    try:
        trace_receipt = verify_trace_report(parsed.run_root)
        final_receipt = verify_final_report(parsed.run_root)
        passed = trace_receipt.passed and final_receipt.passed
        receipt_path = ensure_contained_write_path(
            parsed.out_dir,
            parsed.out_dir / "receipt.json",
            "browser QA receipt path is unsafe",
        )
        write_json(receipt_path, _receipt(parsed, passed))
    except PathSafetyError:
        return int(ExitCode.INCOMPLETE_OR_FAILURE)
    return int(ExitCode.OK) if passed else int(ExitCode.INCOMPLETE_OR_FAILURE)


# main에서 호출된다. 요청된 뷰포트와 검증 결과를 담은 receipt payload를
# 만든다. 실제 브라우저 렌더링은 하지 않았음을 필드로 명시한다.
def _receipt(parsed: _CliNamespace, passed: bool) -> JsonObject:
    viewports: list[JsonValue] = []
    viewports.extend(parsed.viewports)
    return {
        "schema": "report_browser_qa_receipt_v1",
        "status": "pass" if passed else "fail",
        "run_root": str(parsed.run_root.resolve()),
        "viewports": viewports,
        "checked_viewports": viewports,
        "surface_contract": {
            "trace_report": "static_verification",
            "final_report": "static_verification",
            "requested_viewports": "recorded_not_rendered",
        },
        "browser_execution": "not_run",
        "horizontal_overflow": "not_rendered",
        "backend": "deterministic_static_surface",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--run-root", required=True, type=Path)
    _ = parser.add_argument("--viewports", required=True, nargs="+")
    _ = parser.add_argument("--out-dir", required=True, type=Path)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
