from __future__ import annotations

from typing import TYPE_CHECKING

from modules.visual_cue_generation.startup_runner import qwen_model_directory

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_qwen_model_directory_uses_models_file_override(tmp_path: Path) -> None:
    # Given: a project-local Qwen model override.
    _ = (tmp_path / ".models").write_text(
        "VCA_MODEL_QWEN2_5_VL_VISUAL_PATH=mounted/qwen\n", encoding="utf-8"
    )

    # When: visual-cue startup resolves its Qwen path.
    path = qwen_model_directory(tmp_path / "models")

    # Then: it uses the .models path before the default inventory location.
    assert path == tmp_path / "mounted" / "qwen"


def test_qwen_model_directory_prefers_process_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: both project-local and process-local Qwen overrides.
    _ = (tmp_path / ".models").write_text(
        "VCA_MODEL_QWEN2_5_VL_VISUAL_PATH=mounted/qwen\n", encoding="utf-8"
    )
    monkeypatch.setenv("VCA_MODEL_QWEN2_5_VL_VISUAL_PATH", "process/qwen")

    # When: visual-cue startup resolves its Qwen path.
    path = qwen_model_directory(tmp_path / "models")

    # Then: the process environment wins over .models.
    assert path == tmp_path / "process" / "qwen"
