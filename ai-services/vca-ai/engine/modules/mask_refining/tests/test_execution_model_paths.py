from __future__ import annotations

import json
from typing import TYPE_CHECKING

from modules.mask_refining.execution.runner import load_model_entries

if TYPE_CHECKING:
    from pathlib import Path


def test_execution_inventory_uses_models_file_override(tmp_path: Path) -> None:
    # Given: an execution inventory and a project-local SAM2 path override.
    model_cache_root = tmp_path / "models"
    inventory_dir = model_cache_root / "inventory"
    inventory_dir.mkdir(parents=True)
    _ = (inventory_dir / "model_inventory.json").write_text(
        json.dumps(
            {
                "models": [
                    {
                        "key": "sam2.segmenter",
                        "repo_id": "facebook/sam2-hiera-large",
                        "revision": "main",
                        "local_dir": "models/hf/facebook/sam2-hiera-large",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    _ = (tmp_path / ".models").write_text(
        "VCA_MODEL_SAM2_SEGMENTER_PATH=mounted/sam2\n", encoding="utf-8"
    )

    # When: mask refinement loads local model entries.
    entries = load_model_entries(model_cache_root)

    # Then: it receives the shared project-local override path.
    assert entries["sam2.segmenter"].local_dir == tmp_path / "mounted" / "sam2"
