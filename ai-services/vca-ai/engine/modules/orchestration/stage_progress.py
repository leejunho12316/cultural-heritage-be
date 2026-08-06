"""Startup stage progress output helpers."""

from __future__ import annotations

import sys
from typing import Final

from modules.orchestration.receipts import StartupStatus


def _print_started(stage_name: str) -> None:
    _write_progress_line(f"startup: starting {stage_name}")


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
            _print_failed_stage(stage_name, exit_code)
        case StartupStatus.SKIPPED:
            _write_progress_line(f"startup: skipped {stage_name} ({reason})")


print_started: Final = _print_started
print_outcome: Final = _print_outcome


def _print_failed_stage(
    stage_name: str,
    exit_code: int | None,
) -> None:
    _write_progress_line(f"startup: failed {stage_name} (exit_code={exit_code})")


def _write_progress_line(line: str) -> None:
    _ = sys.stdout.write(f"{line}\n")
    _ = sys.stdout.flush()
