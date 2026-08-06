"""Deterministic offline lexical retrieval over validated corpus text."""

from dataclasses import dataclass

from modules.rag.corpus.corpus import CorpusDocumentId, LexicalDocumentInput
from modules.rag.evidence.citations import ChunkId, CitationId, CorpusCitation
from modules.rag.operations.targets import CoverageMetric
from modules.rag.retrieval.terms import QueryTerms
from modules.shared import ContractValidationError


@dataclass(frozen=True, slots=True)
class RetrievalSnippet:
    """Citation/report evidence returned by lexical retrieval."""

    document_id: CorpusDocumentId
    relative_path: str
    snippet_text: str
    matched_terms: tuple[str, ...]
    score: float
    citation: CorpusCitation


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """Ranked lexical evidence with target-level coverage metrics."""

    snippets: tuple[RetrievalSnippet, ...]
    coverage_metrics: tuple[CoverageMetric, ...]


def lexical_retrieve(
    documents: tuple[LexicalDocumentInput, ...],
    terms: QueryTerms,
    top_k: int,
) -> RetrievalResult:
    """Return stable top-k local lexical matches without external services."""
    if top_k < 1:
        field = "top_k"
        reason = "must be at least 1"
        raise ContractValidationError(field, reason)
    tokens = terms.lexical_tokens
    scored = tuple(_snippet(document, tokens) for document in documents)
    matched = tuple(snippet for snippet in scored if snippet.score > 0.0)
    ordered = tuple(
        sorted(
            matched,
            key=lambda snippet: (
                -snippet.score,
                snippet.relative_path,
                snippet.document_id,
            ),
        )
    )
    return RetrievalResult(
        snippets=ordered[:top_k],
        coverage_metrics=_coverage_metrics(len(documents), len(matched)),
    )


def _snippet(
    document: LexicalDocumentInput,
    tokens: tuple[str, ...],
) -> RetrievalSnippet:
    text = document.text
    normalized = text.casefold()
    matched_terms = tuple(token for token in tokens if token in normalized)
    score = float(sum(normalized.count(token) for token in matched_terms))
    relative_path = str(document.relative_path)
    return RetrievalSnippet(
        document_id=document.document_id,
        relative_path=relative_path,
        snippet_text=text,
        matched_terms=matched_terms,
        score=score,
        citation=CorpusCitation(
            citation_id=CitationId(f"{document.document_id}:lexical-citation"),
            chunk_id=ChunkId(f"{document.document_id}:lexical-chunk"),
            source_citation=relative_path,
            source_type="corpus_pdf",
            license_status="internal_review",
            title=document.relative_path.name,
            score=score,
            page_number=None,
        ),
    )


def _coverage_metrics(
    total_documents: int,
    matched_documents: int,
) -> tuple[CoverageMetric, ...]:
    coverage = 0.0 if total_documents == 0 else matched_documents / total_documents
    missing = 1.0 - coverage
    return (
        CoverageMetric("citation_coverage", coverage),
        CoverageMetric("visual_cue_coverage", coverage),
        CoverageMetric("no_citation_rate", missing),
        CoverageMetric("no_visual_cue_rate", missing),
    )
