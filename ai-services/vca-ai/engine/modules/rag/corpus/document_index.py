"""Deterministic cited chunks for extracted document corpus text."""

from dataclasses import dataclass
from pathlib import PurePath
from typing import TypedDict

from modules.rag.corpus.corpus import (
    Corpus,
    CorpusDocumentId,
    CorpusDocumentStatus,
    CorpusPageText,
    CorpusRecord,
    LexicalDocumentInput,
)
from modules.rag.corpus.document_chunking import (
    DEFAULT_OVERLAP_SENTENCES,
    chunk_page_text,
)
from modules.rag.corpus.document_corpus import DOCUMENT_CORPUS_ID
from modules.rag.evidence.citations import ChunkId, CitationId, CorpusCitation
from modules.rag.retrieval.retrieval import RetrievalSnippet
from modules.rag.retrieval.terms import QueryTerms
from modules.shared import ContractValidationError

MIN_SNIPPET_CHARS = 20


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    """One cited, concise text unit from the document corpus index."""

    chunk_id: ChunkId
    document_id: CorpusDocumentId
    relative_path: PurePath
    page_number: int | None
    page_text: str
    snippet_text: str
    citation: CorpusCitation


@dataclass(frozen=True, slots=True)
class DocumentIndex:
    """Cited chunks for lexical compatibility and vector indexing."""

    corpus_id: str
    chunks: tuple[DocumentChunk, ...]


class DocumentIndexJsonRecord(TypedDict):
    """JSONL-compatible cited document-index materialization record."""

    corpus_id: str
    chunk_id: str
    document_id: str
    relative_path: str
    page_number: int | None
    page_text: str | None
    snippet_text: str
    citation_id: str
    source_citation: str
    source_type: str
    license_status: str
    title: str


# 코퍼스 레코드를 인용 가능한 청크 단위로 쪼갠다. startup_runner
# ._locked_corpus_vector_index가 벡터 인덱스를 만들기 전에 호출하며, 실제
# 임베딩 대상은 여기서 만든 chunks다.
def build_document_index(corpus: Corpus, max_snippet_chars: int) -> DocumentIndex:
    """Build deterministic cited chunks from existing corpus records."""
    if max_snippet_chars < MIN_SNIPPET_CHARS:
        field = "max_snippet_chars"
        reason = "must be at least 20"
        raise ContractValidationError(field, reason)
    chunks: list[DocumentChunk] = []
    for record in corpus.records:
        chunks.extend(_record_chunks(record, max_snippet_chars))
    return DocumentIndex(corpus_id=DOCUMENT_CORPUS_ID, chunks=tuple(chunks))


# retrieval.lexical_retrieve와 마찬가지로 실제 startup 파이프라인에서는 쓰이지
# 않는다(청크 검색은 vector_index.vector_retrieve가 담당). 자체 단위 테스트
# 전용 경로다.
def chunks_to_lexical_inputs(
    chunks: tuple[DocumentChunk, ...],
) -> tuple[LexicalDocumentInput, ...]:
    """Expose chunks through the existing lexical retrieval input contract."""
    return tuple(
        LexicalDocumentInput(
            document_id=CorpusDocumentId(chunk.chunk_id),
            relative_path=PurePath(f"{chunk.relative_path}#{chunk.chunk_id}"),
            text=chunk.snippet_text,
        )
        for chunk in chunks
    )


# chunks_to_lexical_inputs와 마찬가지로 비활성 경로: 실제 startup 파이프라인은
# vector_index.vector_retrieve를 쓰고, 이 함수는 자체 단위 테스트에서만
# 실행된다.
def retrieval_snippets_from_chunks(
    chunks: tuple[DocumentChunk, ...],
    terms: QueryTerms,
    top_k: int,
) -> tuple[RetrievalSnippet, ...]:
    """Retrieve cited snippets from indexed chunks without losing citations."""
    if top_k < 1:
        field = "top_k"
        reason = "must be at least 1"
        raise ContractValidationError(field, reason)
    tokens = terms.lexical_tokens
    scored = tuple(_retrieval_snippet(chunk, tokens) for chunk in chunks)
    matched = tuple(snippet for snippet in scored if snippet.score > 0.0)
    return tuple(sorted(matched, key=_retrieval_key))[:top_k]


def document_index_records(
    index: DocumentIndex,
) -> tuple[DocumentIndexJsonRecord, ...]:
    """Convert document index chunks into deterministic JSONL records."""
    return tuple(
        {
            "corpus_id": index.corpus_id,
            "chunk_id": str(chunk.chunk_id),
            "document_id": str(chunk.document_id),
            "relative_path": str(chunk.relative_path),
            "page_number": chunk.page_number,
            "page_text": chunk.page_text if chunk.page_number is not None else None,
            "snippet_text": chunk.snippet_text,
            "citation_id": str(chunk.citation.citation_id),
            "source_citation": chunk.citation.source_citation,
            "source_type": chunk.citation.source_type,
            "license_status": chunk.citation.license_status,
            "title": chunk.citation.title,
        }
        for chunk in index.chunks
    )


# 레코드 상태로 분기: 페이지 단위 텍스트가 있으면 페이지별로 청킹하고,
# (예전 캐시 형식처럼) 페이지 정보 없이 전체 텍스트만 있으면
# _legacy_record_chunks로 대체한다. build_document_index가 레코드마다 호출한다.
def _record_chunks(
    record: CorpusRecord,
    max_snippet_chars: int,
) -> tuple[DocumentChunk, ...]:
    match record.status:
        case CorpusDocumentStatus.INCLUDED_TEXT_PDF:
            if record.pages:
                return _page_chunks(record, max_snippet_chars)
            return _legacy_record_chunks(record, max_snippet_chars)
        case (
            CorpusDocumentStatus.EXCLUDED_NO_OCR
            | CorpusDocumentStatus.EXCLUDED_GARBLED_TEXT
        ):
            return ()


def _page_chunks(
    record: CorpusRecord,
    max_snippet_chars: int,
) -> tuple[DocumentChunk, ...]:
    chunks: list[DocumentChunk] = []
    chunk_index = 1
    for page in record.pages:
        for snippet_text in _snippets(page.text, max_snippet_chars):
            chunk_id = ChunkId(f"{record.document_id}:chunk-{chunk_index:04d}")
            chunks.append(_chunk(record, chunk_id, page, snippet_text))
            chunk_index += 1
    return tuple(chunks)


# 페이지 단위 텍스트가 없는 레코드용 대체 경로(page_number=None인 청크를
# 만든다). 새로 추출된 코퍼스는 항상 pages를 채우므로, 이 경로는 주로 예전
# 캐시 형식과의 하위 호환을 위해 존재한다.
def _legacy_record_chunks(
    record: CorpusRecord,
    max_snippet_chars: int,
) -> tuple[DocumentChunk, ...]:
    text = record.text or ""
    return tuple(
        _chunk(
            record,
            ChunkId(f"{record.document_id}:chunk-{index + 1:04d}"),
            None,
            snippet,
        )
        for index, snippet in enumerate(_snippets(text, max_snippet_chars))
    )


def _chunk(
    record: CorpusRecord,
    chunk_id: ChunkId,
    page: CorpusPageText | None,
    snippet_text: str,
) -> DocumentChunk:
    page_number = None if page is None else page.page_number
    page_text = "" if page is None else page.text
    citation = CorpusCitation(
        citation_id=CitationId(f"{chunk_id}:citation"),
        chunk_id=chunk_id,
        source_citation=str(record.relative_path),
        source_type="corpus_pdf",
        license_status="internal_review",
        title=record.relative_path.name,
        score=0.0,
        page_number=page_number,
    )
    return DocumentChunk(
        chunk_id=chunk_id,
        document_id=record.document_id,
        relative_path=record.relative_path,
        page_number=page_number,
        page_text=page_text,
        snippet_text=snippet_text,
        citation=citation,
    )


def _retrieval_snippet(
    chunk: DocumentChunk,
    tokens: tuple[str, ...],
) -> RetrievalSnippet:
    normalized = chunk.snippet_text.casefold()
    matched_terms = tuple(token for token in tokens if token in normalized)
    score = float(sum(normalized.count(token) for token in matched_terms))
    return RetrievalSnippet(
        document_id=CorpusDocumentId(chunk.chunk_id),
        relative_path=str(chunk.relative_path),
        snippet_text=chunk.snippet_text,
        matched_terms=matched_terms,
        score=score,
        citation=CorpusCitation(
            citation_id=chunk.citation.citation_id,
            chunk_id=chunk.citation.chunk_id,
            source_citation=chunk.citation.source_citation,
            source_type=chunk.citation.source_type,
            license_status=chunk.citation.license_status,
            title=chunk.citation.title,
            score=score,
            page_number=chunk.page_number,
        ),
    )


def _snippets(text: str, max_snippet_chars: int) -> tuple[str, ...]:
    return chunk_page_text(
        text,
        target_chars=max_snippet_chars,
        max_chars=max_snippet_chars,
        overlap_sentences=DEFAULT_OVERLAP_SENTENCES,
    )


def _retrieval_key(snippet: RetrievalSnippet) -> tuple[float, str, str]:
    return (-snippet.score, snippet.relative_path, snippet.document_id)
