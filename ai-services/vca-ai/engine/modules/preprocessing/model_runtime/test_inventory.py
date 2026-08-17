from __future__ import annotations

import json
from typing import TYPE_CHECKING

from modules.preprocessing.model_runtime.inventory import model_paths
from modules.preprocessing.model_runtime.options import split_real_options

if TYPE_CHECKING:
    from pathlib import Path


def test_model_inventory_resolves_relative_local_dir_from_workspace_root(
    tmp_path: Path,
) -> None:
    # Given: a selected <workspace>/models inventory with relative cache paths.
    model_cache_root = tmp_path / "models"
    inventory_dir = model_cache_root / "inventory"
    inventory_dir.mkdir(parents=True)
    _ = (inventory_dir / "model_inventory.json").write_text(
        json.dumps(
            {
                "models": [
                    {
                        "key": "owlv2_sam2.detector",
                        "repo_id": "google/owlv2-base-patch16-ensemble",
                        "revision": "main",
                        "local_dir": "models/hf/google/owlv2-base-patch16-ensemble",
                    },
                    {
                        "key": "sam2.segmenter",
                        "repo_id": "facebook/sam2-hiera-large",
                        "revision": "main",
                        "local_dir": "models/hf/facebook/sam2-hiera-large",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    _, options = split_real_options(("--model-cache-root", str(model_cache_root)))

    # When: preprocessing reads its local inventory.
    paths = model_paths(options)

    # Then: model locations are rooted at the selected workspace.
    assert paths["owlv2_sam2.detector"] == (
        model_cache_root / "hf" / "google" / "owlv2-base-patch16-ensemble"
    )
    assert paths["sam2.segmenter"] == (
        model_cache_root / "hf" / "facebook" / "sam2-hiera-large"
    )
