from __future__ import annotations

import json
from pathlib import Path
from typing import cast

type JsonValue = (
    str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
)


def test_model_inventory_registers_local_qwen_visual_model() -> None:
    # Given: the workspace model inventory consumed by local visual runtimes.
    inventory_path = Path(__file__).parents[3] / "models/inventory/model_inventory.json"
    inventory = cast(
        "JsonValue", json.loads(inventory_path.read_text(encoding="utf-8"))
    )
    assert isinstance(inventory, dict)
    models = inventory.get("models")
    assert isinstance(models, list)

    # When: the Qwen visual entry is located by its inventory key.
    qwen_entry = next(
        entry
        for entry in models
        if isinstance(entry, dict) and entry.get("key") == "qwen2.5-vl.visual"
    )

    # Then: it identifies the locked model and workspace-local cache location.
    assert qwen_entry.get("repo_id") == "Qwen/Qwen2.5-VL-3B-Instruct"
    assert qwen_entry.get("local_dir") == "models/hf/Qwen/Qwen2.5-VL-3B-Instruct"
