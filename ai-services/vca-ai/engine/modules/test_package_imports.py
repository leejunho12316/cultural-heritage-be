from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


PACKAGE_ROOTS: Final[tuple[str, ...]] = (
    "modules",
    "modules.shared",
    "modules.preprocessing",
    "modules.rough_masking",
    "modules.mask_refining",
    "modules.prompt_generating",
    "modules.rag",
    "modules.anomaly_grouping",
    "modules.report_generating",
    "modules.orchestration",
)


def test_package_roots_import_without_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: imports run from an empty working directory that would reveal writes.
    monkeypatch.chdir(tmp_path)
    before_entries = tuple(tmp_path.iterdir())

    # When: every package root is imported.
    for package_name in PACKAGE_ROOTS:
        _ = importlib.import_module(package_name)

    # Then: imports are quiet and do not create files in the working directory.
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert tuple(tmp_path.iterdir()) == before_entries
