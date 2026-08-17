"""Startup stage progress output helpers."""

from __future__ import annotations

import sys
from typing import Final

from modules.orchestration.receipts import StartupStatus


def _print_started(stage_name: str) -> None:
    _write_progress_line(f"startup: starting {stage_name}")


# 스테이지 실행 결과 상태에 맞춰 콘솔에 한 줄 요약을 출력한다.
# stage_execution.py의 실행 루프에서 스테이지마다 호출된다.
def _print_outcome(
    stage_name: str,
    status: StartupStatus,
    exit_code: int | None,
    reason: str | None,
) -> None:
    match status:
        case StartupStatus.COMPLETED:
            _write_progress_line(
                f"startup: completed {stage_name} (exit_code={exit_code})",
            )
        case StartupStatus.FAILED:
            _print_failed_stage(stage_name, exit_code, reason)
        case StartupStatus.SKIPPED:
            _write_progress_line(f"startup: skipped {stage_name} ({reason})")


print_started: Final = _print_started
print_outcome: Final = _print_outcome


# FAILED 상태 전용 출력 포맷을 만든다. reason이 있을 때만 뒤에 덧붙인다.
# _print_outcome에서 status가 FAILED일 때 호출된다.
def _print_failed_stage(
    stage_name: str,
    exit_code: int | None,
    reason: str | None,
) -> None:
    suffix = f", reason={reason}" if reason is not None else ""
    _write_progress_line(
        f"startup: failed {stage_name} (exit_code={exit_code}{suffix})"
    )


def _write_progress_line(line: str) -> None:
    _ = sys.stdout.write(f"{line}\n")
    _ = sys.stdout.flush()
