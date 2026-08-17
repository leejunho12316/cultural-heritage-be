"""Tests for the display-only system-info diagnostics used by the FE footer."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from modules.orchestration.system_info import collect_system_info, main

if TYPE_CHECKING:
    import pytest


def test_collect_system_info_reports_os_device_and_model_inventory() -> None:
    # When: the diagnostics are collected in this real checkout/environment.
    info = collect_system_info()

    # Then: every field the FE footer reads is present and well-typed.
    assert isinstance(info["os"], str)
    assert info["os"]
    assert isinstance(info["pythonVersion"], str)
    assert info["pythonVersion"]
    assert info["device"] in {"cpu", "cuda", "mps", "unknown"}
    assert isinstance(info["libraries"], dict)
    models = info["models"]
    assert isinstance(models, list)
    assert models  # the repo's real model_inventory.json is non-empty
    assert all({"key", "repoId", "revision"} <= model.keys() for model in models)


def test_main_prints_collected_info_as_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # When: the CLI entry point runs (this is how vca-ai shells out to it).
    main()

    # Then: stdout is exactly one JSON object matching collect_system_info.
    captured = json.loads(capsys.readouterr().out)
    assert captured == collect_system_info()
