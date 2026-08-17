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

  # RAG 문서 코퍼스 캐시(models/rag/) 자동 복원. 이미 있으면(예: 이전 기동에서
  # 채워둔 named volume) 아무것도 안 한다 - startup_corpus_cache.py가 캐시
  # 존재 여부만으로 신뢰하고 갱신은 명시적 삭제로만 하게 바뀌었으므로, 여기서도
  # 매번 다시 받지 않고 없을 때만 받는다. VCA_RAG_CORPUS_ARCHIVE_URL이
  # 안 정해져 있으면 이 단계 자체를 건너뛴다(로컬에서 이미 models/rag를
  # 직접 채워둔 경우 등).
  rag_cache_marker="$MODEL_CACHE_ROOT/rag/document_corpus_metadata.jsonl"
  if [ ! -f "$rag_cache_marker" ] && [ -n "${VCA_RAG_CORPUS_ARCHIVE_URL:-}" ]; then
    echo "RAG corpus cache not found at $rag_cache_marker - downloading from VCA_RAG_CORPUS_ARCHIVE_URL"
    rag_file_id="$(printf '%s' "$VCA_RAG_CORPUS_ARCHIVE_URL" | sed -n 's#.*/d/\([^/]*\).*#\1#p')"
    [ -z "$rag_file_id" ] && rag_file_id="$VCA_RAG_CORPUS_ARCHIVE_URL"
    rag_archive="$(mktemp /tmp/vca-rag-corpus.XXXXXX)"
    uv run --with gdown gdown "$rag_file_id" -O "$rag_archive"
    mkdir -p "$MODEL_CACHE_ROOT"
    unzip -q -o "$rag_archive" -d "$MODEL_CACHE_ROOT"
    rm -f "$rag_archive"
    if [ -f "$rag_cache_marker" ]; then
      echo "RAG corpus cache ready at $rag_cache_marker"
    else
      echo "WARNING: RAG corpus archive extracted but $rag_cache_marker is still missing - check archive layout" >&2
    fi
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
