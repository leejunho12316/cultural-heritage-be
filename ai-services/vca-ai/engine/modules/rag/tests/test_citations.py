from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING

import pytest

from modules.rag.evidence.citations import (
    ChunkId,
    CitationExportStatus,
    CitationId,
    CorpusCitation,
    ExportCitation,
    InvalidExportPageError,
    NonExportableCorpusCitation,
    adapt_corpus_citation,
    to_export_citation_bridge,
)
from modules.shared import ExportCitationBridge

if TYPE_CHECKING:
    from collections.abc import Callable


def _corpus_citation(*, page_number: int | None) -> CorpusCitation:
    return CorpusCitation(
        citation_id=CitationId("citation-001"),
        chunk_id=ChunkId("chunk-001"),
        source_citation="stone-conservation.pdf",
        source_type="pdf",
        license_status="internal_research",
        title="Stone Conservation",
        score=0.91,
        page_number=page_number,
    )


def test_valid_corpus_citation_exports_with_rich_metadata() -> None:
    # Given: an internal corpus citation with usable page metadata.
    corpus_citation = _corpus_citation(page_number=12)

    # When: the RAG-owned citation adapter is used.
    result = adapt_corpus_citation(corpus_citation)

    # Then: all rich metadata survives in a valid export citation.
    assert result == ExportCitation(
        citation_id=CitationId("citation-001"),
        chunk_id=ChunkId("chunk-001"),
        source_citation="stone-conservation.pdf",
        source_type="pdf",
        license_status="internal_research",
        title="Stone Conservation",
        score=0.91,
        page_number=12,
    )
    assert result.status is CitationExportStatus.EXPORTED


def test_null_page_citation_is_explicitly_non_exportable() -> None:
    # Given: corpus evidence whose page metadata is unavailable.
    corpus_citation = _corpus_citation(page_number=None)

    # When: the single citation adapter handles it.
    result = adapt_corpus_citation(corpus_citation)

    # Then: the original evidence is quarantined behind an explicit status.
    assert result == NonExportableCorpusCitation(corpus_citation=corpus_citation)
    assert result.status is CitationExportStatus.NON_EXPORTABLE_CORPUS_CITATION


def test_rich_export_citation_downcasts_only_at_bridge_boundary() -> None:
    # Given: a rich export citation produced by the RAG adapter.
    result = adapt_corpus_citation(_corpus_citation(page_number=7))
    assert isinstance(result, ExportCitation)

    # When: relation/report bridge consumption requests the shared shape.
    bridge = to_export_citation_bridge(result)

    # Then: only the identifier and adapter status cross the shared bridge.
    assert bridge == ExportCitationBridge(
        citation_id="citation-001",
        status="exported",
    )
    assert type(bridge) is ExportCitationBridge


def test_all_null_page_support_remains_explicitly_non_exportable() -> None:
    # Given: concept-card-like support made entirely of null-page citations.
    corpus_citations = (
        _corpus_citation(page_number=None),
        _corpus_citation(page_number=None),
    )

    # When: every support citation follows the same scalar adapter path.
    results = tuple(map(adapt_corpus_citation, corpus_citations))

    # Then: none become candidate/report citations and every status is explicit.
    assert all(
        result.status
        is CitationExportStatus.NON_EXPORTABLE_CORPUS_CITATION
        for result in results
    )
    assert not any(isinstance(result, ExportCitation) for result in results)


def test_export_citation_rejects_invalid_page_state() -> None:
    # Given: otherwise rich export metadata with an invalid report page.
    corpus_citation = _corpus_citation(page_number=1)

    # When/Then: direct construction cannot represent page zero.
    with pytest.raises(InvalidExportPageError, match="page_number must be at least 1"):
        _ = ExportCitation(
            citation_id=corpus_citation.citation_id,
            chunk_id=corpus_citation.chunk_id,
            source_citation=corpus_citation.source_citation,
            source_type=corpus_citation.source_type,
            license_status=corpus_citation.license_status,
            title=corpus_citation.title,
            score=corpus_citation.score,
            page_number=0,
        )


@pytest.mark.parametrize(
    ("field_name", "factory"),
    [
        (
            "citation_id",
            lambda: CorpusCitation(
                CitationId(""),
                ChunkId("chunk-001"),
                "stone-conservation.pdf",
                "pdf",
                "internal_research",
                "Stone Conservation",
                0.91,
                1,
            ),
        ),
        (
            "chunk_id",
            lambda: CorpusCitation(
                CitationId("citation-001"),
                ChunkId(""),
                "stone-conservation.pdf",
                "pdf",
                "internal_research",
                "Stone Conservation",
                0.91,
                1,
            ),
        ),
        (
            "source_citation",
            lambda: CorpusCitation(
                CitationId("citation-001"),
                ChunkId("chunk-001"),
                "",
                "pdf",
                "internal_research",
                "Stone Conservation",
                0.91,
                1,
            ),
        ),
        (
            "source_type",
            lambda: CorpusCitation(
                CitationId("citation-001"),
                ChunkId("chunk-001"),
                "stone-conservation.pdf",
                "",
                "internal_research",
                "Stone Conservation",
                0.91,
                1,
            ),
        ),
        (
            "license_status",
            lambda: CorpusCitation(
                CitationId("citation-001"),
                ChunkId("chunk-001"),
                "stone-conservation.pdf",
                "pdf",
                "",
                "Stone Conservation",
                0.91,
                1,
            ),
        ),
        (
            "title",
            lambda: CorpusCitation(
                CitationId("citation-001"),
                ChunkId("chunk-001"),
                "stone-conservation.pdf",
                "pdf",
                "internal_research",
                "",
                0.91,
                1,
            ),
        ),
    ],
)
def test_corpus_citation_rejects_blank_provenance_fields(
    field_name: str,
    factory: Callable[[], CorpusCitation],
) -> None:
    # Given: citation provenance with one blank identity or source field.

    # When/Then: invalid provenance cannot become frozen corpus evidence.
    with pytest.raises(ValueError, match=field_name):
        _ = factory()


@pytest.mark.parametrize("score", [-0.1, float("nan")])
def test_corpus_citation_rejects_invalid_scores(score: float) -> None:
    # Given: citation evidence with a non-reportable score.
    # When/Then: invalid scoring cannot cross the citation boundary.
    with pytest.raises(ValueError, match="score"):
        _ = CorpusCitation(
            citation_id=CitationId("citation-001"),
            chunk_id=ChunkId("chunk-001"),
            source_citation="stone-conservation.pdf",
            source_type="pdf",
            license_status="internal_research",
            title="Stone Conservation",
            score=score,
            page_number=1,
        )


def test_citation_records_are_frozen() -> None:
    # Given: both RAG-owned citation record types.
    corpus_citation = _corpus_citation(page_number=3)
    export_citation = adapt_corpus_citation(corpus_citation)
    assert isinstance(export_citation, ExportCitation)

    # When/Then: citation provenance cannot be mutated after adaptation.
    frozen_setattr = setattr
    with pytest.raises(FrozenInstanceError):
        frozen_setattr(export_citation, "page_number", 4)
