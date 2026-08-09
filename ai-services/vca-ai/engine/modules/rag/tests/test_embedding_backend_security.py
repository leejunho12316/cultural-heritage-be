from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from modules.rag.retrieval.embedding_backend import LocalTransformerTextEmbedder
from modules.shared import ContractValidationError, PathSafetyError

if TYPE_CHECKING:
    from pathlib import Path


def _write_inventory(model_cache_root: Path, local_dir: str, revision: str) -> None:
    inventory_path = model_cache_root / "inventory" / "model_inventory.json"
    inventory_path.parent.mkdir(parents=True)
    payload = {
        "models": [
            {
                "key": "rag.text_embedding",
                "repo_id": "intfloat/multilingual-e5-small",
                "revision": revision,
                "local_dir": local_dir,
            }
        ]
    }
    _ = inventory_path.write_text(json.dumps(payload), encoding="utf-8")


def test_embedding_backend_rejects_model_path_outside_cache(tmp_path: Path) -> None:
    # Given: inventory points the embedding model outside model_cache_root.
    model_cache_root = tmp_path / "models"
    outside_model = tmp_path / "outside-model"
    outside_model.mkdir()
    _write_inventory(model_cache_root, str(outside_model), "sha256:" + "a" * 64)

    # When/Then: backend validation fails before transformer loading.
    with pytest.raises(PathSafetyError):
        _ = LocalTransformerTextEmbedder.from_model_cache(model_cache_root, "cpu")


def test_embedding_backend_rejects_unpinned_revision(tmp_path: Path) -> None:
    # Given: inventory uses a non-pinned local revision label.
    model_cache_root = tmp_path / "models"
    local_model = model_cache_root / "hf" / "intfloat" / "multilingual-e5-small"
    local_model.mkdir(parents=True)
    _write_inventory(
        model_cache_root, "models/hf/intfloat/multilingual-e5-small", "local"
    )

    # When/Then: backend validation requires a pinned hash-style revision.
    with pytest.raises(ContractValidationError) as error:
        _ = LocalTransformerTextEmbedder.from_model_cache(model_cache_root, "cpu")
    assert error.value.field == "model_inventory.models.rag.text_embedding.revision"
