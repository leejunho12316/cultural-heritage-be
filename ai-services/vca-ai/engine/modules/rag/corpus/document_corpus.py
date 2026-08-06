"""Read-only document corpus discovery and text extraction adapter."""

import re
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Protocol, TypedDict

from pdfminer.high_level import extract_pages
from pdfminer.layout import LTChar, LTContainer, LTItem, LTTextContainer

from modules.rag.corpus.cache import CacheFallback, safe_cache_target
from modules.rag.corpus.corpus import (
    CorpusDocumentStatus,
    CorpusMetadataRow,
    CorpusPageText,
)
from modules.rag.corpus.extraction_cache import (
    append_extraction_cache,
    read_extraction_cache,
)

DOCUMENT_CORPUS_ID = "document_sweep_260pdf_253text"
SOURCE_DOCUMENT_ROOT = Path("/Users/csc9211/Downloads/dataset/document")
MANIFEST_FILENAME = "nrich_preservation_manifest.jsonl"


class DocumentTextExtractor(Protocol):
    """PDF text extraction capability used by corpus builders."""

    def extract_pages(self, pdf_path: Path) -> tuple[CorpusPageText, ...]:
        """Return deterministic page text extracted from one PDF path."""
        ...


class CorpusPageTextJsonRecord(TypedDict):
    """JSONL-compatible source page text materialization record."""

    page_number: int
    text: str


class CorpusMetadataJsonRecord(TypedDict):
    """JSONL-compatible corpus metadata materialization record."""

    document_id: str
    relative_path: str
    status: str
    text: str | None
    pages: tuple[CorpusPageTextJsonRecord, ...]


@dataclass(frozen=True, slots=True)
class PdfTextExtractor:
    """Default local PDF text extractor backed by pdfminer.six."""

    def extract_pages(self, pdf_path: Path) -> tuple[CorpusPageText, ...]:
        """Extract page text locally without OCR, network, or model calls."""
        pages: list[CorpusPageText] = []
        for page_number, layout in enumerate(extract_pages(pdf_path), start=1):
            text_parts = tuple(_layout_text(element) for element in layout)
            text = _normalize_text("".join(text_parts))
            if text:
                pages.append(CorpusPageText(page_number=page_number, text=text))
        return tuple(pages)


@dataclass(frozen=True, slots=True)
class DocumentCorpusConfig:
    """Configuration for read-only source corpus adaptation."""

    source_root: Path = SOURCE_DOCUMENT_ROOT
    extraction_cache: CacheFallback | None = None


def discover_document_pdfs(source_root: Path) -> tuple[Path, ...]:
    """Return source PDFs in stable relative-path order."""
    return tuple(
        sorted(path.relative_to(source_root) for path in source_root.glob("*.pdf"))
    )


def read_manifest_titles(source_root: Path) -> dict[str, str]:
    """Read optional manifest titles keyed by filename."""
    manifest_path = source_root / MANIFEST_FILENAME
    if not manifest_path.exists():
        return {}
    titles: dict[str, str] = {}
    with manifest_path.open(encoding="utf-8") as manifest_file:
        for line in manifest_file:
            filename = _json_string_field(line, "filename")
            title = _json_string_field(line, "title")
            if filename is not None and title is not None:
                _ = titles.setdefault(filename, title)
    return titles


def build_document_corpus(
    config: DocumentCorpusConfig,
    extractor: DocumentTextExtractor,
) -> tuple[CorpusMetadataRow, ...]:
    """Build existing CorpusMetadataRow records from source PDFs."""
    extraction_cache = resolve_extraction_cache_target(config)
    cached_pages = {
        entry.relative_path: entry.pages
        for entry in read_extraction_cache(extraction_cache)
    }
    rows: list[CorpusMetadataRow] = []
    for relative_path in discover_document_pdfs(config.source_root):
        pdf_path = config.source_root / relative_path
        pages = cached_pages.get(relative_path)
        if pages is None:
            pages = extractor.extract_pages(pdf_path)
            append_extraction_cache(extraction_cache, relative_path, pages)
        rows.append(_metadata_row(relative_path, pages))
    return tuple(rows)


def _metadata_row(
    relative_path: PurePath,
    pages: tuple[CorpusPageText, ...],
) -> CorpusMetadataRow:
    text = _normalize_text(" ".join(page.text for page in pages))
    status = (
        CorpusDocumentStatus.INCLUDED_TEXT_PDF
        if text
        else CorpusDocumentStatus.EXCLUDED_NO_OCR
    )
    return CorpusMetadataRow(
        document_id=_document_id(relative_path),
        relative_path=str(relative_path),
        status=status.value,
        text=text or None,
        pages=pages,
    )


def resolve_extraction_cache_target(config: DocumentCorpusConfig) -> Path | None:
    """Resolve optional extraction cache through the shared path-safety boundary."""
    if config.extraction_cache is None:
        return None
    return safe_cache_target(config.source_root, config.extraction_cache)


def corpus_metadata_records(
    rows: tuple[CorpusMetadataRow, ...],
) -> tuple[CorpusMetadataJsonRecord, ...]:
    """Convert corpus metadata rows into deterministic JSONL records."""
    return tuple(
        {
            "document_id": row.document_id,
            "relative_path": row.relative_path,
            "status": row.status,
            "text": row.text,
            "pages": tuple(
                {"page_number": page.page_number, "text": page.text}
                for page in row.pages
            ),
        }
        for row in rows
    )


def _document_id(relative_path: PurePath) -> str:
    return relative_path.stem


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _layout_text(element: LTItem) -> str:
    match element:
        case LTTextContainer():
            return element.get_text()
        case LTChar():
            return element.get_text()
        case LTContainer():
            return "".join(_layout_text(child) for child in element)
        case _:
            return ""


def _json_string_field(line: str, field: str) -> str | None:
    pattern = rf'"{field}"\s*:\s*"([^"]*)"'
    match = re.search(pattern, line)
    if match is None:
        return None
    return match.group(1)
