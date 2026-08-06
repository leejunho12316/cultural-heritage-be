"""Validated corpus metadata and deterministic lexical indexing inputs."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePath
from typing import NewType, NoReturn, Self

from modules.shared import ContractValidationError

CorpusDocumentId = NewType("CorpusDocumentId", str)


def _raise_contract(field: str, reason: str) -> NoReturn:
    raise ContractValidationError(field, reason)


class CorpusDocumentStatus(StrEnum):
    """Supported source-document states for local lexical indexing."""

    INCLUDED_TEXT_PDF = "included_text_pdf"
    EXCLUDED_NO_OCR = "excluded_no_ocr"


@dataclass(frozen=True, slots=True)
class CorpusPageText:
    """Normalized text extracted from one source PDF page."""

    page_number: int
    text: str

    def __post_init__(self) -> None:
        """Reject invalid page provenance before indexing."""
        if self.page_number < 1:
            _raise_contract("page_number", "must be at least 1")
        if not self.text.strip():
            _raise_contract("page_text", "must not be blank")


@dataclass(frozen=True, slots=True)
class CorpusMetadataRow:
    """Unparsed corpus metadata received from an optional read-only adapter."""

    document_id: str
    relative_path: str
    status: str
    text: str | None
    pages: tuple[CorpusPageText, ...] = ()


@dataclass(frozen=True, slots=True)
class CorpusRecord:
    """Validated PDF metadata retained by the local corpus."""

    document_id: CorpusDocumentId
    relative_path: PurePath
    status: CorpusDocumentStatus
    text: str | None
    pages: tuple[CorpusPageText, ...] = ()

    def __post_init__(self) -> None:
        """Reject invalid PDF paths and status/text combinations."""
        if not self.document_id.strip():
            _raise_contract("document_id", "must not be blank")
        if (
            self.relative_path == PurePath()
            or self.relative_path.is_absolute()
            or ".." in self.relative_path.parts
            or self.relative_path.suffix.lower() != ".pdf"
        ):
            _raise_contract("relative_path", "must be a contained relative PDF path")
        match self.status:
            case CorpusDocumentStatus.INCLUDED_TEXT_PDF:
                if self.text is None or not self.text.strip():
                    _raise_contract("text", "included text PDFs require non-blank text")
            case CorpusDocumentStatus.EXCLUDED_NO_OCR:
                if self.text is not None:
                    _raise_contract(
                        "text", "excluded no-OCR PDFs must not contain text"
                    )
                if self.pages:
                    _raise_contract(
                        "pages", "excluded no-OCR PDFs must not contain pages"
                    )


@dataclass(frozen=True, slots=True)
class LexicalDocumentInput:
    """Text-bearing corpus document ready for later local lexical indexing."""

    document_id: CorpusDocumentId
    relative_path: PurePath
    text: str


@dataclass(frozen=True, slots=True)
class CorpusAccounting:
    """Deterministic source-PDF accounting for a validated corpus."""

    attempted_pdfs: int
    included_text_pdfs: int
    excluded_no_ocr: int

    def __post_init__(self) -> None:
        """Reject negative or internally inconsistent accounting totals."""
        if min(self.attempted_pdfs, self.included_text_pdfs, self.excluded_no_ocr) < 0:
            _raise_contract("accounting", "counts must be non-negative")
        if self.attempted_pdfs != self.included_text_pdfs + self.excluded_no_ocr:
            _raise_contract(
                "accounting", "attempted PDFs must equal included plus excluded"
            )


def parse_corpus_record(metadata: CorpusMetadataRow) -> CorpusRecord:
    """Parse one external metadata row into a validated immutable record."""
    try:
        status = CorpusDocumentStatus(metadata.status)
    except ValueError as error:
        field = "status"
        reason = f"unsupported value {metadata.status!r}"
        raise ContractValidationError(field, reason) from error
    return CorpusRecord(
        document_id=CorpusDocumentId(metadata.document_id),
        relative_path=PurePath(metadata.relative_path),
        status=status,
        text=metadata.text,
        pages=metadata.pages,
    )


@dataclass(frozen=True, slots=True)
class Corpus:
    """A duplicate-free corpus with deterministic accounting and text inputs."""

    records: tuple[CorpusRecord, ...]

    def __post_init__(self) -> None:
        """Reject repeated document identifiers or paths."""
        seen_document_ids: set[CorpusDocumentId] = set()
        seen_paths: set[PurePath] = set()
        for record in self.records:
            if record.document_id in seen_document_ids:
                _raise_contract("records", "duplicate document_id")
            if record.relative_path in seen_paths:
                _raise_contract("records", "duplicate relative_path")
            seen_document_ids.add(record.document_id)
            seen_paths.add(record.relative_path)

    @classmethod
    def from_metadata(cls, rows: tuple[CorpusMetadataRow, ...]) -> Self:
        """Parse complete external metadata before exposing a corpus."""
        return cls(tuple(parse_corpus_record(row) for row in rows))

    @property
    def lexical_inputs(self) -> tuple[LexicalDocumentInput, ...]:
        """Return included PDFs in the original metadata order."""
        inputs: list[LexicalDocumentInput] = []
        for record in self.records:
            match record.status:
                case CorpusDocumentStatus.INCLUDED_TEXT_PDF:
                    text = record.text
                    if text is None:
                        _raise_contract(
                            "text", "included text PDFs require non-blank text"
                        )
                    inputs.append(
                        LexicalDocumentInput(
                            document_id=record.document_id,
                            relative_path=record.relative_path,
                            text=text,
                        )
                    )
                case CorpusDocumentStatus.EXCLUDED_NO_OCR:
                    continue
        return tuple(inputs)

    @property
    def accounting(self) -> CorpusAccounting:
        """Return counts derived solely from the immutable corpus records."""
        included_count = len(self.lexical_inputs)
        return CorpusAccounting(
            attempted_pdfs=len(self.records),
            included_text_pdfs=included_count,
            excluded_no_ocr=len(self.records) - included_count,
        )
