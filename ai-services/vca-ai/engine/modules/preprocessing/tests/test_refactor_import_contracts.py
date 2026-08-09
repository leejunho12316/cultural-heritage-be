from __future__ import annotations

# pyright: reportAny=false
import importlib
from pathlib import Path

from modules import preprocessing


def test_preprocessing_public_and_role_import_contracts() -> None:
    # Given: public, CLI, and role-based preprocessing module paths.
    module_paths = (
        "modules.preprocessing",
        "modules.preprocessing.pipeline",
        "modules.preprocessing.contracts.records",
        "modules.preprocessing.contracts.views",
        "modules.preprocessing.preflight.request",
        "modules.preprocessing.assets.materialization",
        "modules.preprocessing.model_runtime.runtime",
    )

    # When: every supported path is imported.
    imported = {path: importlib.import_module(path) for path in module_paths}

    # Then: public exports, role modules, and pipeline seams remain.
    assert (
        preprocessing.ViewRecord
        is imported["modules.preprocessing.contracts.views"].ViewRecord
    )
    assert hasattr(imported["modules.preprocessing.contracts.records"], "DetectionBox")
    assert (
        preprocessing.ScaleMetadata
        is imported["modules.preprocessing.contracts.views"].ScaleMetadata
    )
    pipeline = imported["modules.preprocessing.pipeline"]
    for name in (
        "prepare_detector_input",
        "load_runtime_models",
        "run_runtime_detection",
        "resolve_runtime_device",
        "load_model_inventory",
        "validate_model_cache",
        "write_detection_assets",
    ):
        assert hasattr(pipeline, name)


def test_preprocessing_root_contains_only_facade_and_cli() -> None:
    # Given: preprocessing implementation files live in role subpackages.
    root = next(
        parent
        for parent in Path(__file__).resolve().parents
        if parent.name == "preprocessing"
    )

    # When: root-level Python files are listed.
    root_files = {path.name for path in root.glob("*.py")}

    # Then: root contains only the package facade and CLI entrypoint.
    assert root_files == {"__init__.py", "pipeline.py"}
