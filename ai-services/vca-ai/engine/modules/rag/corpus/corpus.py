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
    EXCLUDED_GARBLED_TEXT = "excluded_garbled_text"


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
            case (
                CorpusDocumentStatus.EXCLUDED_NO_OCR
                | CorpusDocumentStatus.EXCLUDED_GARBLED_TEXT
            ):
                if self.text is not None:
                    _raise_contract("text", "excluded PDFs must not contain text")
                if self.pages:
                    _raise_contract("pages", "excluded PDFs must not contain pages")


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
    excluded_garbled_text: int

    def __post_init__(self) -> None:
        """Reject negative or internally inconsistent accounting totals."""
        counts = (
            self.attempted_pdfs,
            self.included_text_pdfs,
            self.excluded_no_ocr,
            self.excluded_garbled_text,
        )
        if min(counts) < 0:
            _raise_contract("accounting", "counts must be non-negative")
        if self.attempted_pdfs != (
            self.included_text_pdfs + self.excluded_no_ocr + self.excluded_garbled_text
        ):
            _raise_contract(
                "accounting", "attempted PDFs must equal included plus excluded"
            )


# 외부(문서 코퍼스 어댑터/캐시)에서 온 미검증 메타데이터 한 행을 검증된
# CorpusRecord로 변환한다. Corpus.from_metadata가 각 행마다 호출한다.
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

    # 코퍼스를 만드는 유일한 공개 진입점. startup_runner._locked_corpus_vector_index
    # 가 startup_corpus_rows로 읽은 원시 메타데이터를 여기로 넘긴다.
    @classmethod
    def from_metadata(cls, rows: tuple[CorpusMetadataRow, ...]) -> Self:
        """Parse complete external metadata before exposing a corpus."""
        return cls(tuple(parse_corpus_record(row) for row in rows))

    # 인덱싱 가능한(OCR/깨짐 없이 텍스트 추출 성공한) PDF만 골라낸다.
    # 현재는 아래 accounting 프로퍼티가 포함 문서 수를 셀 때만 내부적으로
    # 사용한다(문서 청킹 자체는 document_index.py가 record 상태를 직접 본다).
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
                case (
                    CorpusDocumentStatus.EXCLUDED_NO_OCR
                    | CorpusDocumentStatus.EXCLUDED_GARBLED_TEXT
                ):
                    continue
        return tuple(inputs)

    # 코퍼스 상태를 즉석에서 다시 세어 캐시된 카운트 없이 항상 최신 값을
    # 돌려준다. 별도로 저장/캐시되지 않는 파생값이라는 점에 유의.
    @property
    def accounting(self) -> CorpusAccounting:
        """Return counts derived solely from the immutable corpus records."""
        included_count = len(self.lexical_inputs)
        garbled_count = sum(
            1
            for record in self.records
            if record.status is CorpusDocumentStatus.EXCLUDED_GARBLED_TEXT
        )
        return CorpusAccounting(
            attempted_pdfs=len(self.records),
            included_text_pdfs=included_count,
            excluded_no_ocr=len(self.records) - included_count - garbled_count,
            excluded_garbled_text=garbled_count,
        )
