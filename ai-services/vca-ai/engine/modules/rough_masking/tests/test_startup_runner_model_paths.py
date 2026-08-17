from __future__ import annotations

from typing import TYPE_CHECKING

from modules.rough_masking.startup_manifest import load_model_entries
from modules.rough_masking.tests._startup_runner_support import (
    make_request,
    write_model_inventory,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_startup_runner_inventory_uses_models_file_override(tmp_path: Path) -> None:
    # Given: a rough-mask startup inventory and a project-local detector override.
    request = make_request(tmp_path)
    write_model_inventory(request)
    _ = (tmp_path / ".models").write_text(
        "VCA_MODEL_OWLV2_SAM2_DETECTOR_PATH=mounted/owlv2\n", encoding="utf-8"
    )

    # When: the startup runner's inventory loader reads model entries.
    entries = load_model_entries(request.model_cache_root)

    # Then: the detector uses the .models location before its inventory local_dir.
    assert entries["owlv2_sam2.detector"].local_dir == tmp_path / "mounted" / "owlv2"
