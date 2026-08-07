# Shared Model Cache

This directory is the workspace-local cache for heavyweight local models shared by pipeline modules.

Weights and downloaded snapshots are intentionally ignored by git. Keep only small inventory files under `models/inventory/` so every module can agree on model IDs, revisions, and local paths without committing model binaries.

Expected layout:

```text
models/
  hf/
    <repo-id-as-path>/
  inventory/
    model_inventory.json
```

Preprocessing owns detector/SAM2 execution in this workspace variant. RAG owns
the local text-embedding model for vector retrieval. Other modules should
consume the inventory and generated artifacts rather than downloading their own
model copies.

## Local Path Overrides

Copy [`.models.example`](../.models.example) to a project-root `.models` file to
override one or more local model directories. `.models` is ignored by git and
uses one non-empty `KEY=VALUE` entry per line; blank lines and lines beginning
with `#` are ignored. Malformed lines and empty paths fail validation rather
than falling back to another location.

Relative paths are resolved from the workspace root, defined as the parent of
the active model cache root. With the default `models` cache root, that is the
repository root.

For each inventory model key, resolution is:

1. Process environment variable, such as `VCA_MODEL_QWEN2_5_VL_VISUAL_PATH` or `VCA_MODEL_RAG_TEXT_EMBEDDING_PATH`.
2. Matching entry in the project-root `.models` file.
3. `local_dir` from `models/inventory/model_inventory.json`.

This only selects an already-local directory. It does not enable model
downloads or any remote Hugging Face fallback.
