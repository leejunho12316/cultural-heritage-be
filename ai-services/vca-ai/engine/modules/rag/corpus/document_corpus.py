"""Read-only document corpus discovery and text extraction adapter."""

import re
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Final, Protocol, TypedDict

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
from modules.shared import PathSafetyError

DOCUMENT_CORPUS_ID = "document_sweep_260pdf_253text"
MANIFEST_FILENAME = "nrich_preservation_manifest.jsonl"

# Some PDFs embed a custom font encoding pdfminer cannot resolve to Unicode;
# it then falls back to decoding raw glyph codes as Mac OS Roman bytes,
# producing well-formed but wrong characters (bullets, math operators,
# accented Latin) at a density real Korean/English technical prose never
# reaches. Calibrated against the real document corpus: genuinely garbled
# documents measured 15-34%, the next-highest legitimate document (heavy
# with italicized Latin species names) measured 4.3%.
_GARBLED_TEXT_RATIO_THRESHOLD: Final = 0.08
_MAC_ROMAN_HIGH_BYTE_CHARACTERS: Final = frozenset(
    bytes(range(0x80, 0x100)).decode("mac_roman")
)


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
    """Configuration for read-only source corpus adaptation.

    `source_root` has no built-in default - callers must supply it explicitly
    (the startup path requires the VCA_DOCUMENT_CORPUS_DIR environment
    variable via `startup_document_source_root()`) so this never silently
    falls back to a path that only exists on one developer's machine.
    """

    source_root: Path
    extraction_cache: CacheFallback | None = None


# 소스 루트 바로 아래 PDF만 훑는다(하위 디렉터리 재귀 없음). build_document_corpus
# 와 startup_corpus_cache._source_fingerprint 둘 다 동일한 목록을 얻기 위해
# 이 함수를 호출한다.
def discover_document_pdfs(source_root: Path) -> tuple[Path, ...]:
    """Return source PDFs in stable relative-path order."""
    return tuple(
        sorted(
            _safe_pdf_relative_path(source_root, path)
            for path in source_root.glob("*.pdf")
        )
    )


# 선택적 보존 매니페스트에서 파일명→제목 매핑을 읽는다. 매니페스트가 없으면
# 조용히 빈 dict를 반환한다(제목이 필수는 아님).
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


# 소스 PDF들을 순회하며 추출 캐시를 먼저 확인하고, 캐시에 없으면 실제로
# 텍스트를 추출해 캐시에 추가한다. startup_corpus_cache.startup_corpus_rows가
# 상위 캐시(코퍼스 전체 캐시)까지 없을 때만 이 함수를 호출한다.
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


# 추출된 페이지 텍스트로부터 포함/제외 상태를 결정한다: 텍스트가 없으면
# NO_OCR, 깨진 비율이 임계값을 넘으면 GARBLED_TEXT, 아니면 포함.
# build_document_corpus가 PDF마다 호출한다.
def _metadata_row(
    relative_path: PurePath,
    pages: tuple[CorpusPageText, ...],
) -> CorpusMetadataRow:
    text = _normalize_text(" ".join(page.text for page in pages))
    if not text:
        status = CorpusDocumentStatus.EXCLUDED_NO_OCR
    elif _is_garbled_text(text):
        status = CorpusDocumentStatus.EXCLUDED_GARBLED_TEXT
    else:
        status = CorpusDocumentStatus.INCLUDED_TEXT_PDF
    included = status is CorpusDocumentStatus.INCLUDED_TEXT_PDF
    return CorpusMetadataRow(
        document_id=_document_id(relative_path),
        relative_path=str(relative_path),
        status=status.value,
        text=text if included else None,
        pages=pages if included else (),
    )


def _is_garbled_text(text: str) -> bool:
    """Detect PDF text mangled by an unresolved custom font encoding."""
    hits = sum(1 for character in text if character in _MAC_ROMAN_HIGH_BYTE_CHARACTERS)
    return hits / len(text) > _GARBLED_TEXT_RATIO_THRESHOLD


# extraction_cache가 설정된 경우에만 실제 안전 경로로 해석한다.
# build_document_corpus가 캐시 조회/기록 전에 호출한다.
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


# 심볼릭 링크이거나 source_root 밖으로 벗어난 PDF를 거부한다.
# discover_document_pdfs가 발견한 각 경로를 검증할 때 호출한다.
def _safe_pdf_relative_path(source_root: Path, path: Path) -> Path:
    resolved_root = source_root.expanduser().resolve()
    if path.is_symlink():
        reason = "source PDF symlinks are forbidden"
        raise PathSafetyError(str(path), reason)
    resolved_pdf = path.expanduser().resolve()
    if not _is_contained(resolved_pdf, resolved_root):
        reason = "source PDF escapes source document root"
        raise PathSafetyError(str(path), reason)
    return path.relative_to(source_root)


def _is_contained(candidate: Path, root: Path) -> bool:
    return candidate == root or candidate.is_relative_to(root)


def _normalize_text(text: str) -> str:
    # pdfminer can emit lone UTF-16 surrogates for some broken PDF fonts/
    # encodings. Strip them here, once, so every downstream consumer
    # (hashing, JSON writes, tokenization) only ever sees valid Unicode.
    sanitized = text.encode("utf-8", errors="ignore").decode("utf-8")
    return " ".join(sanitized.split())


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
