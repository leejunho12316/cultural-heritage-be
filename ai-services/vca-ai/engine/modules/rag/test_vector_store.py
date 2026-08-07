from __future__ import annotations

from pathlib import PurePath
from typing import TYPE_CHECKING

import numpy as np
import pytest

from modules.rag.corpus.corpus import CorpusDocumentId
from modules.rag.corpus.document_index import DocumentChunk
from modules.rag.evidence.citations import ChunkId, CitationId, CorpusCitation
from modules.rag.retrieval import vector_store
from modules.rag.retrieval.vector_index import VectorIndex
from modules.rag.retrieval.vector_store import write_vector_index_artifacts
from modules.shared import PathSafetyError

if TYPE_CHECKING:
    from pathlib import Path


def _index() -> VectorIndex:
    return _index_with("chunk-1", (1.0, 0.0))


def _index_with(chunk_name: str, embedding: tuple[float, float]) -> VectorIndex:
    chunk_id = ChunkId("chunk-1")
    chunk = DocumentChunk(
        chunk_id=chunk_id,
        document_id=CorpusDocumentId(chunk_name),
        relative_path=PurePath("stone.pdf"),
        page_number=2,
        page_text="white powdery deposit",
        snippet_text=chunk_name,
        citation=CorpusCitation(
            citation_id=CitationId("citation-1"),
            chunk_id=chunk_id,
            source_citation="stone.pdf",
            source_type="corpus_pdf",
            license_status="internal_review",
            title="stone.pdf",
            score=0.0,
            page_number=2,
        ),
    )
    return VectorIndex(
        chunks=(chunk,),
        embeddings=(embedding,),
        model_id="test-embedder",
    )


def test_vector_index_writer_rejects_symlink_leaf(tmp_path: Path) -> None:
    # Given: a vector-index leaf points outside the model cache root.
    model_cache_root = tmp_path / "models"
    root = model_cache_root / "rag" / "vector_index"
    root.mkdir(parents=True)
    external_file = tmp_path.parent / f"{tmp_path.name}-external.npy"
    _ = external_file.write_bytes(b"sentinel")
    (root / "embeddings.npy").symlink_to(external_file)

    # When/Then: vector index writing fails before following the symlink.
    with pytest.raises(PathSafetyError):
        write_vector_index_artifacts(model_cache_root, _index())
    assert external_file.read_bytes() == b"sentinel"


def test_vector_index_writer_rejects_symlink_parent(tmp_path: Path) -> None:
    # Given: the vector-index directory is redirected outside the model cache root.
    model_cache_root = tmp_path / "models"
    rag_root = model_cache_root / "rag"
    rag_root.mkdir(parents=True)
    external_root = tmp_path.parent / f"{tmp_path.name}-external-vector"
    external_root.mkdir()
    (rag_root / "vector_index").symlink_to(external_root, target_is_directory=True)

    # When/Then: vector index writing rejects the symlinked parent.
    with pytest.raises(PathSafetyError):
        write_vector_index_artifacts(model_cache_root, _index())
    assert not (external_root / "manifest.json").exists()


def test_vector_index_writer_materializes_numpy_embeddings(tmp_path: Path) -> None:
    # Given: a safe model cache root.
    model_cache_root = tmp_path / "models"

    # When: vector index artifacts are written.
    write_vector_index_artifacts(model_cache_root, _index())

    # Then: the persisted embeddings are usable by NumPy tooling.
    embeddings_path = model_cache_root / "rag/vector_index/embeddings.npy"
    np.load(embeddings_path)
    assert embeddings_path.read_bytes().startswith(b"\x93NUMPY")


def test_vector_index_writer_preserves_previous_index_when_chunk_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a complete previous vector index exists.
    model_cache_root = tmp_path / "models"
    write_vector_index_artifacts(model_cache_root, _index_with("old-chunk", (0.0, 1.0)))
    vector_root = model_cache_root / "rag" / "vector_index"
    old_embeddings = (vector_root / "embeddings.npy").read_bytes()

    def fail_jsonl(
        path: Path, rows: tuple[vector_store.VectorChunkJsonRecord, ...]
    ) -> None:
        _ = path, rows
        reason = "chunk write failed"
        raise OSError(reason)

    monkeypatch.setattr(vector_store, "_write_jsonl_atomic", fail_jsonl)

    # When: a later index write fails after staging begins.
    with pytest.raises(OSError, match="chunk write failed"):
        write_vector_index_artifacts(
            model_cache_root, _index_with("new-chunk", (1.0, 0.0))
        )

    # Then: the final artifact set still describes the previous complete index.
    chunks = (vector_root / "chunks.jsonl").read_text(encoding="utf-8")
    assert (vector_root / "embeddings.npy").read_bytes() == old_embeddings
    assert "old-chunk" in chunks
    assert "new-chunk" not in chunks
