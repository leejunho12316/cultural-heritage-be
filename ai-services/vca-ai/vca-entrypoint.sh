#!/bin/sh
set -eu

ENGINE_ROOT="${VCA_ENGINE_ROOT:-/vca_v2}"
MODEL_CACHE_ROOT="${VCA_MODEL_CACHE_ROOT:-/opt/vca-models/models}"

if [ -f "$ENGINE_ROOT/pyproject.toml" ]; then
  uv sync --project "$ENGINE_ROOT" --group vision --no-install-project
  mkdir -p "$MODEL_CACHE_ROOT/inventory"
  if [ -f "$ENGINE_ROOT/models/inventory/model_inventory.json" ]; then
    cp "$ENGINE_ROOT/models/inventory/model_inventory.json" "$MODEL_CACHE_ROOT/inventory/model_inventory.json"
  fi
  if [ "${VCA_BOOTSTRAP_MODELS:-false}" = "true" ]; then
    uv run --project "$ENGINE_ROOT" python - "$MODEL_CACHE_ROOT" <<'PY'
import json
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

model_cache_root = Path(sys.argv[1])
workspace_root = model_cache_root.parent
inventory_path = model_cache_root / "inventory" / "model_inventory.json"
inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
for model in inventory["models"]:
    local_dir = Path(model["local_dir"])
    target = local_dir if local_dir.is_absolute() else workspace_root / local_dir
    revision = model["revision"]
    snapshot_download(
        repo_id=model["repo_id"],
        revision=None if revision.startswith("sha256:") else revision,
        local_dir=target,
    )
PY
  fi
fi

exec "$@"
