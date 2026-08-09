"""Standalone CLI for anomaly grouping JSON requests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING

from modules.anomaly_grouping.io import read_request, write_result
from modules.anomaly_grouping.pipeline import run_anomaly_grouping
from modules.shared import (
    ContractValidationError,
    ExitCode,
    PathSafetyError,
    ensure_no_symlink_leaf,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


class _CliNamespace(argparse.Namespace):
    input_json: Path
    output_json: Path

    def __init__(self) -> None:
        super().__init__()
        self.input_json = Path()
        self.output_json = Path()


# 독립 실행 anomaly_grouping CLI의 진입점(__main__에서 호출). 입력 JSON을 읽어
# 파이프라인을 돌리고 결과를 출력 JSON에 기록한 뒤 종료 코드를 반환한다.
def main(arguments: Sequence[str] | None = None) -> int:
    """Run the standalone JSON request/response CLI."""
    parsed = _CliNamespace()
    _ = _parser().parse_args(arguments, namespace=parsed)
    try:
        symlink_reason = "anomaly grouping output is a symlink"
        output_path = ensure_no_symlink_leaf(
            parsed.output_json,
            symlink_reason,
        )
        result = run_anomaly_grouping(read_request(parsed.input_json))
        write_result(output_path, result)
    except (
        ContractValidationError,
        FileNotFoundError,
        json.JSONDecodeError,
        PathSafetyError,
    ):
        return int(ExitCode.INCOMPLETE_OR_FAILURE)
    return int(ExitCode.OK)


# main에서 사용하는 --input-json/--output-json 인자 파서를 만든다.
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--input-json", required=True, type=Path)
    _ = parser.add_argument("--output-json", required=True, type=Path)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
