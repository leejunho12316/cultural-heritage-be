from __future__ import annotations

from pathlib import Path

import pytest

from modules.shared import ContractValidationError
from modules.shared.model_paths import (
    model_path_environment_variable,
    parse_model_paths,
    resolve_model_path,
)


def test_parse_model_paths_ignores_blank_lines_and_comments(tmp_path: Path) -> None:
    # Given: a project-local config with comments and one model path.
    config_path = tmp_path / ".models"
    _ = config_path.write_text(
        "\n# local model paths\n\nVCA_MODEL_QWEN2_5_VL_VISUAL_PATH=models/qwen\n",
        encoding="utf-8",
    )

    # When: model-path overrides are parsed.
    paths = parse_model_paths(config_path)

    # Then: only configured environment-style keys are returned.
    assert paths == {"VCA_MODEL_QWEN2_5_VL_VISUAL_PATH": "models/qwen"}


def test_parse_model_paths_rejects_malformed_nonempty_line(tmp_path: Path) -> None:
    # Given: a config line that does not have KEY=VALUE form.
    config_path = tmp_path / ".models"
    _ = config_path.write_text("VCA_MODEL_QWEN2_5_VL_VISUAL_PATH\n", encoding="utf-8")

    # When / Then: parsing rejects the malformed configuration boundary.
    with pytest.raises(ContractValidationError, match="line 1"):
        _ = parse_model_paths(config_path)


def test_parse_model_paths_rejects_empty_configured_path(tmp_path: Path) -> None:
    # Given: an explicit model override without a path.
    config_path = tmp_path / ".models"
    _ = config_path.write_text(
        "VCA_MODEL_QWEN2_5_VL_VISUAL_PATH=   \n", encoding="utf-8"
    )

    # When / Then: parsing rejects an unusable local path.
    with pytest.raises(ContractValidationError, match="must not be empty"):
        _ = parse_model_paths(config_path)


def test_model_path_environment_variable_derives_qwen_name() -> None:
    # Given: the Qwen inventory key.

    # When: its override environment variable is derived.
    environment_variable = model_path_environment_variable("qwen2.5-vl.visual")

    # Then: operators can use the documented stable variable name.
    assert environment_variable == "VCA_MODEL_QWEN2_5_VL_VISUAL_PATH"


def test_resolve_model_path_uses_workspace_relative_inventory_path(
    tmp_path: Path,
) -> None:
    # Given: a conventional <workspace>/models cache and relative inventory path.
    model_cache_root = tmp_path / "models"

    # When: no process or .models override is present.
    path = resolve_model_path(
        "qwen2.5-vl.visual",
        Path("models/hf/Qwen/Qwen2.5-VL-3B-Instruct"),
        model_cache_root,
    )

    # Then: the inventory path is resolved from the workspace root.
    assert path == model_cache_root / "hf" / "Qwen" / "Qwen2.5-VL-3B-Instruct"


def test_resolve_model_path_uses_models_file_override(tmp_path: Path) -> None:
    # Given: a .models override relative to the workspace root.
    model_cache_root = tmp_path / "models"
    _ = (tmp_path / ".models").write_text(
        "VCA_MODEL_QWEN2_5_VL_VISUAL_PATH=mounted/qwen\n", encoding="utf-8"
    )

    # When: the Qwen path is resolved without a process override.
    path = resolve_model_path(
        "qwen2.5-vl.visual",
        Path("models/hf/Qwen/Qwen2.5-VL-3B-Instruct"),
        model_cache_root,
    )

    # Then: the project-local override wins over inventory.
    assert path == tmp_path / "mounted" / "qwen"


def test_resolve_model_path_prefers_process_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: conflicting project-local and process-local Qwen overrides.
    model_cache_root = tmp_path / "models"
    _ = (tmp_path / ".models").write_text(
        "VCA_MODEL_QWEN2_5_VL_VISUAL_PATH=mounted/qwen\n", encoding="utf-8"
    )
    monkeypatch.setenv("VCA_MODEL_QWEN2_5_VL_VISUAL_PATH", "process/qwen")

    # When: the Qwen path is resolved.
    path = resolve_model_path(
        "qwen2.5-vl.visual",
        Path("models/hf/Qwen/Qwen2.5-VL-3B-Instruct"),
        model_cache_root,
    )

    # Then: the process environment takes precedence.
    assert path == tmp_path / "process" / "qwen"


def test_resolve_model_path_rejects_empty_process_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a process-level override with no configured local path.
    monkeypatch.setenv("VCA_MODEL_QWEN2_5_VL_VISUAL_PATH", "")

    # When / Then: the invalid higher-precedence configuration is rejected.
    with pytest.raises(ContractValidationError, match="must not be empty"):
        _ = resolve_model_path(
            "qwen2.5-vl.visual",
            Path("models/hf/Qwen/Qwen2.5-VL-3B-Instruct"),
            tmp_path / "models",
        )


def test_resolve_model_path_rejects_malformed_models_file_with_process_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a process override alongside malformed project-local configuration.
    _ = (tmp_path / ".models").write_text("not-a-key-value-line\n", encoding="utf-8")
    monkeypatch.setenv("VCA_MODEL_QWEN2_5_VL_VISUAL_PATH", "process/qwen")

    # When / Then: the resolver rejects invalid configuration at the boundary.
    with pytest.raises(ContractValidationError, match="line 1"):
        _ = resolve_model_path(
            "qwen2.5-vl.visual",
            Path("models/hf/Qwen/Qwen2.5-VL-3B-Instruct"),
            tmp_path / "models",
        )
