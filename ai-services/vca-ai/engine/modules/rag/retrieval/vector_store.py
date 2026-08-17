"""Persist exact vector-index artifacts for startup RAG retrieval."""

from __future__ import annotations

import json
import shutil
from hashlib import sha256
from typing import TYPE_CHECKING, TypedDict, cast

import numpy as np

from modules.rag.retrieval.vector_index import VectorIndex
from modules.shared import (
    ContractValidationError,
    ensure_contained_write_path,
    ensure_no_symlink_leaf,
    ensure_no_symlink_path_components,
)
from modules.shared.json_object import parse_json_object_for_field

if TYPE_CHECKING:
    from pathlib import Path

    from numpy.typing import NDArray

    from modules.rag.corpus.document_index import DocumentChunk
    from modules.rag.retrieval.vector_index import FloatMatrix

_MANIFEST_READ_FIELD = "vector_index_manifest"

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
    corpus_hash: str


def vector_index_dir(model_cache_root: Path) -> Path:
    """Return the local vector-index artifact directory."""
    return model_cache_root / "rag" / VECTOR_INDEX_DIR_NAME


# startup_runner._load_or_build_vector_index가 새로 임베딩을 계산하기 전에
# 먼저 호출하는 캐시 조회 지점. 캐시 판정 기준(모델/청크 개수/코퍼스 해시)은
# 아래 docstring 참고.
def read_vector_index_artifacts(
    model_cache_root: Path,
    *,
    expected_model_id: str,
    expected_chunks: tuple[DocumentChunk, ...],
) -> VectorIndex | None:
    """Return the persisted vector index only if it exactly matches the corpus.

    Any missing, unreadable, or mismatched artifact is treated as a cache
    miss rather than an error: the caller falls back to re-embedding.
    """
    if not expected_chunks:
        return None
    root = vector_index_dir(model_cache_root)
    _guard_existing_vector_paths(model_cache_root, root)
    manifest = _matching_manifest(
        root / VECTOR_MANIFEST_NAME,
        expected_model_id=expected_model_id,
        expected_chunk_count=len(expected_chunks),
        expected_corpus_hash=_corpus_hash(expected_chunks),
    )
    if manifest is None:
        return None
    try:
        embeddings = _read_embeddings(
            root / VECTOR_EMBEDDINGS_NAME,
            expected_rows=len(expected_chunks),
            expected_dimension=manifest["embedding_dimension"],
        )
    except (OSError, ValueError):
        return None
    return VectorIndex(
        chunks=expected_chunks, embeddings=embeddings, model_id=manifest["model_id"]
    )


# 저장된 manifest.json이 스키마/모델/청크개수/코퍼스해시 4가지 모두 기대값과
# 일치할 때만 유효한 캐시로 인정한다. 하나라도 어긋나면 None을 반환해
# read_vector_index_artifacts가 캐시 미스로 처리하게 한다.
def _matching_manifest(
    manifest_path: Path,
    *,
    expected_model_id: str,
    expected_chunk_count: int,
    expected_corpus_hash: str,
) -> VectorManifestJson | None:
    try:
        payload = parse_json_object_for_field(
            manifest_path.read_text(encoding="utf-8"), _MANIFEST_READ_FIELD
        )
    except (OSError, ContractValidationError):
        return None
    schema = payload.get("schema")
    model_id = payload.get("model_id")
    chunk_count = payload.get("chunk_count")
    embedding_dimension = payload.get("embedding_dimension")
    corpus_hash = payload.get("corpus_hash")
    if not (
        isinstance(schema, str)
        and isinstance(model_id, str)
        and isinstance(chunk_count, int)
        and isinstance(embedding_dimension, int)
        and isinstance(corpus_hash, str)
    ):
        return None
    if (
        schema != VECTOR_INDEX_SCHEMA
        or model_id != expected_model_id
        or chunk_count != expected_chunk_count
        or corpus_hash != expected_corpus_hash
    ):
        return None
    return {
        "schema": schema,
        "model_id": model_id,
        "chunk_count": chunk_count,
        "embedding_dimension": embedding_dimension,
        "corpus_hash": corpus_hash,
    }


def _read_embeddings(
    path: Path, *, expected_rows: int, expected_dimension: int
) -> FloatMatrix:
    array = cast("NDArray[np.float32]", np.load(path))
    if array.shape != (expected_rows, expected_dimension):
        message = "persisted vector index embeddings shape mismatch"
        raise ValueError(message)
    # VectorIndex.__post_init__ normalizes embeddings to a numpy array on
    # construction regardless of what's passed in, so returning the loaded
    # array as-is (instead of round-tripping through .tolist() into Python
    # tuples here, just to be converted straight back to numpy a moment
    # later) skips a redundant O(rows*dimension) conversion pass.
    return cast("FloatMatrix", array)


def _corpus_hash(chunks: tuple[DocumentChunk, ...]) -> str:
    # Extracted PDF text can legitimately contain lone UTF-16 surrogates
    # (a known pdfminer quirk on some fonts/encodings). This hash only
    # needs a stable byte representation for cache invalidation, not
    # standards-valid UTF-8, so unpaired surrogates are passed through
    # rather than raising.
    payload = "\n".join(f"{chunk.chunk_id}\t{chunk.snippet_text}" for chunk in chunks)
    return sha256(payload.encode("utf-8", errors="surrogatepass")).hexdigest()


# startup_runner._load_or_build_vector_index가 캐시 미스 뒤 새로 만든 인덱스를
# 저장할 때 호출한다. stage(임시)+backup 디렉터리를 거쳐 원자적으로 교체하며,
# 실패 시 이전 인덱스를 복구한다(아래 _publish_staged_vector_index 참고).
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


# 기존 인덱스를 backup으로 옮기고 새로 만든 stage를 실제 경로로 교체한다.
# 교체 도중 실패하면 backup을 원래 자리로 되돌려, 인덱스가 없는 상태로
# 남지 않도록 한다.
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
        "corpus_hash": _corpus_hash(index.chunks),
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
