"""Exact local vector index for cited document chunks."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from typing import TYPE_CHECKING, Protocol, cast

import numpy as np

from modules.rag.evidence.citations import CorpusCitation
from modules.rag.retrieval.retrieval import RetrievalSnippet
from modules.shared import ContractValidationError

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from modules.rag.corpus.document_index import DocumentChunk

type FloatVector = tuple[float, ...]
type FloatMatrix = tuple[FloatVector, ...]
QUERY_STOPWORDS = frozenset({"a", "an", "of", "on", "or", "the"})


class TextEmbedder(Protocol):
    """Embedding interface used by exact local vector retrieval."""

    @property
    def model_id(self) -> str:
        """Return the stable embedding model identity for index compatibility."""
        ...

    def embed_passages(self, texts: tuple[str, ...]) -> FloatMatrix:
        """Embed passage chunks into normalized float32 vectors."""
        ...

    def embed_query(self, text: str) -> FloatVector:
        """Embed one retrieval query into a normalized float32 vector."""
        ...


@dataclass(frozen=True, slots=True)
class VectorIndex:
    """In-memory exact cosine index with citation-bearing chunk metadata."""

    chunks: tuple[DocumentChunk, ...]
    # 생성 시점(빌드 경로든, 캐시에서 읽는 경로든, 테스트에서 직접 생성하는
    # 경로든 전부 __post_init__을 거친다)에 numpy 배열로 정규화된다. 타입은
    # 호출부 호환을 위해 FloatMatrix로 유지하지만 런타임 값은 항상
    # NDArray[np.float32]다 - vector_retrieve의 코사인 유사도 계산을 행렬곱
    # 한 번으로 끝내기 위함(청크마다 파이썬 루프로 내적을 돌리면 코퍼스가
    # 클 때 쿼리 하나당 수십 초가 걸렸다).
    embeddings: FloatMatrix
    model_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "embeddings", np.asarray(self.embeddings, dtype=np.float32)
        )


@dataclass(frozen=True, slots=True)
class InMemoryTextEmbedder:
    """Deterministic test embedder for vector retrieval contracts."""

    passage_vectors: dict[str, FloatVector]
    query_vectors: dict[str, FloatVector]
    model_id: str

    def embed_passages(self, texts: tuple[str, ...]) -> FloatMatrix:
        """Return configured passage vectors in request order."""
        return _normalize_matrix(tuple(self.passage_vectors[text] for text in texts))

    def embed_query(self, text: str) -> FloatVector:
        """Return the configured query vector."""
        return _normalize_vector(self.query_vectors[text])


# 코퍼스 청크 전체를 임베딩해 인메모리 벡터 인덱스를 만든다.
# startup_runner._load_or_build_vector_index가 캐시 미스일 때 호출한다.
def build_vector_index(
    chunks: tuple[DocumentChunk, ...], embedder: TextEmbedder
) -> VectorIndex:
    """Embed document chunks into an exact local vector index."""
    if not chunks:
        field = "document_chunks"
        reason = "must not be empty"
        raise ContractValidationError(field, reason)
    embeddings = embedder.embed_passages(tuple(chunk.snippet_text for chunk in chunks))
    if len(embeddings) != len(chunks):
        field = "embeddings"
        reason = "row count must match chunk count"
        raise ContractValidationError(field, reason)
    _validate_matrix(embeddings)
    return VectorIndex(chunks=chunks, embeddings=embeddings, model_id=embedder.model_id)


# RAG 스테이지가 실제로 쓰는 검색 함수(retrieval.lexical_retrieve는 비활성
# 경로). 코사인 유사도로 top_k개를 정렬해 반환하며, startup_runner가 각
# 프롬프트 쿼리마다 호출한다.
def vector_retrieve(
    index: VectorIndex, query_text: str, embedder: TextEmbedder, *, top_k: int
) -> tuple[RetrievalSnippet, ...]:
    """Return top-k semantic matches with original corpus citations."""
    if top_k < 1:
        field = "top_k"
        reason = "must be at least 1"
        raise ContractValidationError(field, reason)
    if embedder.model_id != index.model_id:
        field = "embedding_model"
        reason = "query embedder must match index embedder"
        raise ContractValidationError(field, reason)
    query = embedder.embed_query(query_text)
    _validate_vector(query)
    embeddings = cast("NDArray[np.float32]", index.embeddings)
    query_array = np.asarray(query, dtype=np.float32)
    if embeddings.shape[1] != query_array.shape[0]:
        field = "embedding_vector"
        reason = "query and passage dimensions must match"
        raise ContractValidationError(field, reason)
    # 벡터가 이미 정규화되어 있으므로(embed_passages/embed_query가 항상
    # 정규화된 벡터를 반환) 단순 내적이 곧 코사인 유사도다. 코퍼스 전체에
    # 대한 내적을 청크마다 파이썬 루프로 도는 대신 행렬곱 한 번(BLAS)으로
    # 계산한다.
    score_values = embeddings @ query_array
    ranked_indices = tuple(
        sorted(
            range(len(index.chunks)),
            key=lambda item: (-float(score_values[item]), item),
        )
    )
    return tuple(
        _snippet(index.chunks[item], float(score_values[item]), query_text)
        for item in ranked_indices[:top_k]
    )


# 청크 하나를 RetrievalSnippet으로 감싼다. matched_terms는 임베딩 유사도와는
# 별개로, 표시용 하이라이트를 위해 아래 _matched_terms가 어휘 일치로 따로
# 계산한다(순위 자체는 임베딩 점수로만 결정됨).
def _snippet(chunk: DocumentChunk, score: float, query_text: str) -> RetrievalSnippet:
    citation = CorpusCitation(
        citation_id=chunk.citation.citation_id,
        chunk_id=chunk.citation.chunk_id,
        source_citation=chunk.citation.source_citation,
        source_type=chunk.citation.source_type,
        license_status=chunk.citation.license_status,
        title=chunk.citation.title,
        score=score,
        page_number=chunk.page_number,
    )
    return RetrievalSnippet(
        document_id=chunk.document_id,
        relative_path=str(chunk.relative_path),
        snippet_text=chunk.snippet_text,
        matched_terms=_matched_terms(query_text, chunk.snippet_text),
        score=score,
        citation=citation,
    )


def _matched_terms(query_text: str, snippet_text: str) -> tuple[str, ...]:
    normalized = snippet_text.casefold()
    tokens = tuple(
        dict.fromkeys(
            token.strip(".,:;()[]{}-_/!").casefold()
            for token in query_text.split()
        )
    )
    return tuple(
        token
        for token in tokens
        if token and token not in QUERY_STOPWORDS and token in normalized
    )


def _normalize_matrix(matrix: FloatMatrix) -> FloatMatrix:
    return tuple(_normalize_vector(vector) for vector in matrix)


def _normalize_vector(vector: FloatVector) -> FloatVector:
    _validate_vector(vector)
    norm = sqrt(sum(value * value for value in vector))
    denominator = max(norm, 1e-12)
    return tuple(value / denominator for value in vector)


def _validate_matrix(matrix: FloatMatrix) -> None:
    if not matrix:
        field = "embeddings"
        reason = "must not be empty"
        raise ContractValidationError(field, reason)
    dimension = len(matrix[0])
    for vector in matrix:
        if len(vector) != dimension:
            field = "embeddings"
            reason = "all rows must have the same dimension"
            raise ContractValidationError(field, reason)
        _validate_vector(vector)


def _validate_vector(vector: FloatVector) -> None:
    if not vector or not all(isfinite(value) for value in vector):
        field = "embedding_vector"
        reason = "must contain finite dimensions"
        raise ContractValidationError(field, reason)
