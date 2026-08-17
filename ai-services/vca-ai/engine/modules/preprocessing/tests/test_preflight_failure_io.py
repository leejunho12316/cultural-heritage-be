from __future__ import annotations

# pyright: reportAny=false
import json
from typing import TYPE_CHECKING

from modules import preprocessing
from modules.preprocessing.pipeline import run

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_directory_input_writes_json_safe_preflight_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a directory is accidentally passed where image files are expected.
    workspace = tmp_path / "workspace"
    input_dir = workspace / "inputs"
    input_dir.mkdir(parents=True)
    run_root = workspace / "runs" / "preflight-failure"
    monkeypatch.chdir(workspace)

    # When: preprocessing reaches the preflight failure writer.
    exit_code = run(
        (
            str(input_dir.relative_to(workspace)),
            "--run-root",
            str(run_root.relative_to(workspace)),
            "--dry-run",
        )
    )

    # Then: failure details are JSON-safe and contain string paths, not Path objects.
    receipt: dict[str, str | int | list[dict[str, str]]] = json.loads(
        (run_root / "receipts" / "preflight.json").read_text()
    )
    assert exit_code == preprocessing.ExitCode.INCOMPLETE_OR_FAILURE
    assert receipt["workspace_root"] == str(workspace.resolve())
    assert receipt["run_root"] == str(run_root.resolve())
    assert receipt["exit_code"] == int(preprocessing.ExitCode.INCOMPLETE_OR_FAILURE)
    assert receipt["issues"] == [
        {
            "field": "image_path",
            "reason": f"not a readable file: {input_dir.resolve()}",
        }
    ]
