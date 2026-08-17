"""RAG-owned citation records and their export adapter."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import NewType, NoReturn, override

from modules.shared import ContractValidationError, ExportCitationBridge

CitationId = NewType("CitationId", str)
ChunkId = NewType("ChunkId", str)


@unique
class CitationExportStatus(StrEnum):
    """Closed citation outcomes owned by the RAG adapter."""

    EXPORTED = "exported"
    NON_EXPORTABLE_CORPUS_CITATION = "non_exportable_corpus_citation"


@dataclass(frozen=True, slots=True)
class CorpusCitation:
    """Rich internal retrieval evidence that may lack page metadata."""

    citation_id: CitationId
    chunk_id: ChunkId
    source_citation: str
    source_type: str
    license_status: str
    title: str
    score: float
    page_number: int | None

    def __post_init__(self) -> None:
        """Enforce complete citation provenance before indexing."""
        _validate_nonblank("citation_id", self.citation_id)
        _validate_nonblank("chunk_id", self.chunk_id)
        _validate_nonblank("source_citation", self.source_citation)
        _validate_nonblank("source_type", self.source_type)
        _validate_nonblank("license_status", self.license_status)
        _validate_nonblank("title", self.title)
        _validate_score(self.score)


@dataclass(frozen=True, slots=True)
class InvalidExportPageError(Exception):
    """An attempted export used a page outside the report citation domain."""

    page_number: int

    @override
    def __str__(self) -> str:
        """Describe the invalid page constraint."""
        return f"page_number must be at least 1, got {self.page_number}"


@dataclass(frozen=True, slots=True)
class ExportCitation:
    """Rich candidate/report citation with guaranteed positive page metadata."""

    citation_id: CitationId
    chunk_id: ChunkId
    source_citation: str
    source_type: str
    license_status: str
    title: str
    score: float
    page_number: int

    def __post_init__(self) -> None:
        """Reject page states forbidden in candidate/report provenance."""
        _validate_nonblank("citation_id", self.citation_id)
        _validate_nonblank("chunk_id", self.chunk_id)
        _validate_nonblank("source_citation", self.source_citation)
        _validate_nonblank("source_type", self.source_type)
        _validate_nonblank("license_status", self.license_status)
        _validate_nonblank("title", self.title)
        _validate_score(self.score)
        if self.page_number < 1:
            raise InvalidExportPageError(page_number=self.page_number)

    @property
    def status(self) -> CitationExportStatus:
        """Return the adapter-owned status for bridge consumers."""
        return CitationExportStatus.EXPORTED


@dataclass(frozen=True, slots=True)
class NonExportableCorpusCitation:
    """Quarantined internal evidence that cannot enter report provenance."""

    corpus_citation: CorpusCitation

    @property
    def status(self) -> CitationExportStatus:
        """Return the explicit reason this evidence was not exported."""
        return CitationExportStatus.NON_EXPORTABLE_CORPUS_CITATION


type CitationAdapterResult = ExportCitation | NonExportableCorpusCitation


# 내부 CorpusCitation을 보고서에 내보낼 수 있는 형태로 변환한다. page_number가
# 없으면 NonExportableCorpusCitation으로 격리된다. concept_cards.RagConceptEvidence
# .from_retrieval과 relations/evidence 빌더가 인용을 내보낼 때 호출한다.
def adapt_corpus_citation(citation: CorpusCitation) -> CitationAdapterResult:
    """Convert internal citation evidence through the sole rich export path."""
    if citation.page_number is None:
        return NonExportableCorpusCitation(corpus_citation=citation)
    return ExportCitation(
        citation_id=citation.citation_id,
        chunk_id=citation.chunk_id,
        source_citation=citation.source_citation,
        source_type=citation.source_type,
        license_status=citation.license_status,
        title=citation.title,
        score=citation.score,
        page_number=citation.page_number,
    )


# ExportCitation을 공유 브리지 계약(ExportCitationBridge)으로 축소 변환한다.
# evidence.py의 _export_bridges가 관계/보고서 빌더로 넘길 때 호출한다.
def to_export_citation_bridge(citation: ExportCitation) -> ExportCitationBridge:
    """Downcast rich export evidence for relation/report bridge consumption."""
    return ExportCitationBridge(
        citation_id=citation.citation_id,
        status=citation.status.value,
    )


def _validate_nonblank(field: str, value: str) -> None:
    if not value.strip():
        _raise_contract(field, "must not be blank")


def _validate_score(score: float) -> None:
    if not math.isfinite(score) or score < 0.0:
        _raise_contract("score", "must be finite and non-negative")


def _raise_contract(field: str, reason: str) -> NoReturn:
    raise ContractValidationError(field, reason)
