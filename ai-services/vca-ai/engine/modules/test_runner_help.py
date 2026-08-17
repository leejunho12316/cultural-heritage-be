from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class HelpCommand:
    module: str
    expected_text: str


HELP_COMMANDS = (
    HelpCommand(
        "modules.orchestration.startup", "--allow-unverified-model-hashes-local-only"
    ),
    HelpCommand(
        "modules.preprocessing.pipeline", "Real preprocessing execution manual"
    ),
    HelpCommand("modules.mask_refining.refinement_cli", "--prompt-output-dir"),
    HelpCommand("modules.anomaly_grouping.runner", "--input-json"),
    HelpCommand("modules.report_generating.runner", "--output-json"),
    HelpCommand("modules.report_generating.browser_qa", "--viewports"),
)


def test_runner_help_commands_exit_zero_without_stderr() -> None:
    # Given: every standalone pipeline CLI module.
    modules = HELP_COMMANDS

    # When: each CLI is asked for usage help.
    results = tuple(_run_help(command.module) for command in modules)

    # Then: help exits cleanly and prints the expected command-specific option.
    assert [result.returncode for result in results] == [0] * len(modules)
    assert [result.stderr for result in results] == [""] * len(modules)
    for command, result in zip(modules, results, strict=True):
        assert command.expected_text in result.stdout
    assert "--db-stages" not in results[0].stdout


def _run_help(module: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        (sys.executable, "-m", module, "--help"),
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )
