"""Persist exact vector-index artifacts for startup RAG retrieval."""

from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING, TypedDict

import numpy as np

from modules.shared import (
    ensure_contained_write_path,
    ensure_no_symlink_leaf,
    ensure_no_symlink_path_components,
)

if TYPE_CHECKING:
    from pathlib import Path

    from modules.rag.retrieval.vector_index import VectorIndex

VECTOR_INDEX_DIR_NAME = "vector_index"
VECTOR_MANIFEST_NAME = "manifest.json"
VECTOR_CHUNKS_NAME = "chunks.jsonl"
VECTOR_EMBEDDINGS_NAME = "embeddings.npy"
VECTOR_INDEX_SCHEMA = "vca-rag-vector-index-v1"


class VectorChunkJsonRecord(TypedDict):
    """JSONL metadata row paired by offset with the embedding matrix."""

    chunk_id: str
    citation_id: str
    document_id: str
    relative_path: str
    page_number: int | None
    snippet_text: str
    source_citation: str


class VectorManifestJson(TypedDict):
    """JSON manifest for validating persisted vector-index artifacts."""

    schema: str
    model_id: str
    chunk_count: int
    embedding_dimension: int


def vector_index_dir(model_cache_root: Path) -> Path:
    """Return the local vector-index artifact directory."""
    return model_cache_root / "rag" / VECTOR_INDEX_DIR_NAME


def write_vector_index_artifacts(model_cache_root: Path, index: VectorIndex) -> None:
    """Persist embeddings and chunk metadata for inspection and reuse."""
    root = vector_index_dir(model_cache_root)
    stage_root = root.with_name(f"{root.name}.tmp")
    backup_root = root.with_name(f"{root.name}.old")
    _guard_vector_directory(model_cache_root, root)
    _guard_vector_directory(model_cache_root, stage_root)
    _guard_vector_directory(model_cache_root, backup_root)
    _guard_existing_vector_paths(model_cache_root, root)
    root.parent.mkdir(parents=True, exist_ok=True)
    _remove_tree(stage_root)
    _remove_tree(backup_root)
    try:
        stage_root.mkdir(parents=True)
        _write_embeddings_atomic(stage_root / VECTOR_EMBEDDINGS_NAME, index)
        _write_jsonl_atomic(stage_root / VECTOR_CHUNKS_NAME, _chunk_records(index))
        _write_text_atomic(
            stage_root / VECTOR_MANIFEST_NAME,
            json.dumps(_manifest(index), sort_keys=True, separators=(",", ":")) + "\n",
        )
        _publish_staged_vector_index(root, stage_root, backup_root)
    except OSError:
        _remove_tree(stage_root)
        raise


def _publish_staged_vector_index(
    root: Path, stage_root: Path, backup_root: Path
) -> None:
    try:
        if root.exists():
            _ = root.replace(backup_root)
        _ = stage_root.replace(root)
    except OSError:
        if not root.exists() and backup_root.exists():
            _ = backup_root.replace(root)
        raise
    finally:
        _remove_tree(stage_root)
        _remove_tree(backup_root)


def _chunk_records(index: VectorIndex) -> tuple[VectorChunkJsonRecord, ...]:
    return tuple(
        {
            "chunk_id": str(chunk.chunk_id),
            "citation_id": str(chunk.citation.citation_id),
            "document_id": str(chunk.document_id),
            "relative_path": str(chunk.relative_path),
            "page_number": chunk.page_number,
            "snippet_text": chunk.snippet_text,
            "source_citation": chunk.citation.source_citation,
        }
        for chunk in index.chunks
    )


def _manifest(index: VectorIndex) -> VectorManifestJson:
    dimension = len(index.embeddings[0])
    return {
        "schema": VECTOR_INDEX_SCHEMA,
        "model_id": index.model_id,
        "chunk_count": len(index.chunks),
        "embedding_dimension": dimension,
    }


def _guard_vector_path(model_cache_root: Path, path: Path) -> None:
    reason = "vector index artifact path escapes or uses symlinks"
    _ = ensure_contained_write_path(model_cache_root, path, reason)
    _ = ensure_no_symlink_leaf(path, reason)
    _ = ensure_no_symlink_leaf(path.with_suffix(f"{path.suffix}.tmp"), reason)


def _guard_vector_directory(model_cache_root: Path, path: Path) -> None:
    reason = "vector index artifact directory escapes or uses symlinks"
    _ = ensure_no_symlink_path_components(path, reason)
    _ = ensure_contained_write_path(model_cache_root, path, reason)


def _guard_existing_vector_paths(model_cache_root: Path, root: Path) -> None:
    _guard_vector_path(model_cache_root, root / VECTOR_EMBEDDINGS_NAME)
    _guard_vector_path(model_cache_root, root / VECTOR_CHUNKS_NAME)
    _guard_vector_path(model_cache_root, root / VECTOR_MANIFEST_NAME)


def _write_embeddings_atomic(path: Path, index: VectorIndex) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        with temporary.open("wb") as output_file:
            np.save(output_file, np.asarray(index.embeddings, dtype=np.float32))
        _ = temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _write_jsonl_atomic(path: Path, rows: tuple[VectorChunkJsonRecord, ...]) -> None:
    payload = "\n".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows
    )
    _write_text_atomic(path, f"{payload}\n")


def _write_text_atomic(path: Path, payload: str) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        _ = temporary.write_text(payload, encoding="utf-8")
        _ = temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
        return
    path.unlink()
