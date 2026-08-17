from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Final

import pytest

from modules.rag.corpus.cache import CacheFallback
from modules.rag.corpus.corpus import Corpus, CorpusDocumentStatus, CorpusPageText
from modules.rag.corpus.document_corpus import (
    DOCUMENT_CORPUS_ID,
    DocumentCorpusConfig,
    PdfTextExtractor,
    build_document_corpus,
    corpus_metadata_records,
    discover_document_pdfs,
    read_manifest_titles,
    resolve_extraction_cache_target,
)
from modules.rag.qwen.qwen_bridge_json import parse_json_object
from modules.shared import PathSafetyError

SOURCE_DOCUMENT_ROOT = Path("/Users/csc9211/Downloads/dataset/document")
INTERRUPTED_EXTRACTION_MESSAGE: Final = "interrupted extraction"


@dataclass(frozen=True, slots=True)
class FakeExtractor:
    extracted_pages: dict[str, tuple[CorpusPageText, ...]]

    def extract_pages(self, pdf_path: Path) -> tuple[CorpusPageText, ...]:
        return self.extracted_pages.get(pdf_path.name, ())


class InterruptedExtractionError(OSError):
    pass


@dataclass(frozen=True, slots=True)
class FailingExtractor:
    extracted_pages: dict[str, tuple[CorpusPageText, ...]]
    fail_on: str

    def extract_pages(self, pdf_path: Path) -> tuple[CorpusPageText, ...]:
        if pdf_path.name == self.fail_on:
            raise InterruptedExtractionError(INTERRUPTED_EXTRACTION_MESSAGE)
        return self.extracted_pages.get(pdf_path.name, ())


@dataclass(frozen=True, slots=True)
class RecordingExtractor:
    extracted_pages: dict[str, tuple[CorpusPageText, ...]]
    calls: list[str]

    def extract_pages(self, pdf_path: Path) -> tuple[CorpusPageText, ...]:
        self.calls.append(pdf_path.name)
        return self.extracted_pages.get(pdf_path.name, ())


def _extraction_cache_fallback(tmp_path: Path) -> CacheFallback:
    return CacheFallback(
        allowed_root=tmp_path / "models",
        root=tmp_path / "models" / "rag",
        relative_path=PurePath("document_corpus_extracted_pages.jsonl"),
    )


def _write_source_pdfs(source_root: Path, filenames: tuple[str, ...]) -> None:
    source_root.mkdir()
    for filename in filenames:
        _ = (source_root / filename).write_bytes(b"%PDF fixture")


def test_real_document_source_discovery_counts_all_pdfs_stably() -> None:
    # Given: the user-provided document corpus directory.
    # When: RAG discovers source PDFs without reading or writing them.
    pdfs = discover_document_pdfs(SOURCE_DOCUMENT_ROOT)

    # Then: all attempted PDFs are represented in stable relative-path order.
    assert len(pdfs) == 260
    assert pdfs == tuple(sorted(pdfs))
    assert all(path.suffix.lower() == ".pdf" for path in pdfs)


def test_manifest_titles_are_optional_metadata_not_attempted_corpus_truth() -> None:
    # Given: the user-provided manifest next to the PDFs.
    # When: manifest titles are read for enrichment.
    titles = read_manifest_titles(SOURCE_DOCUMENT_ROOT)

    # Then: manifest data is available but PDF discovery remains the corpus truth.
    assert (
        titles["9168_714941_보존과학연구 제47집 1호.pdf"] == "보존과학연구 제47집 1호"
    )
    assert len(discover_document_pdfs(SOURCE_DOCUMENT_ROOT)) == 260


def test_build_document_corpus_returns_existing_corpus_rows_from_extracted_text(
    tmp_path: Path,
) -> None:
    # Given: local PDF placeholders and deterministic extracted text.
    source_root = tmp_path / "document"
    source_root.mkdir()
    _ = (source_root / "a.pdf").write_bytes(b"%PDF fixture")
    _ = (source_root / "b.pdf").write_bytes(b"%PDF fixture")
    extractor = FakeExtractor(
        {
            "a.pdf": (
                CorpusPageText(
                    page_number=2,
                    text="white powdery deposit on stone surface",
                ),
            )
        }
    )

    # When: RAG builds corpus metadata from the read-only source tree.
    metadata_rows = build_document_corpus(
        DocumentCorpusConfig(source_root=source_root),
        extractor,
    )
    corpus = Corpus.from_metadata(metadata_rows)

    # Then: text-bearing PDFs become lexical inputs and blank PDFs are excluded.
    assert DOCUMENT_CORPUS_ID == "document_sweep_260pdf_253text"
    assert tuple(row.status for row in metadata_rows) == (
        CorpusDocumentStatus.INCLUDED_TEXT_PDF.value,
        CorpusDocumentStatus.EXCLUDED_NO_OCR.value,
    )
    assert corpus.lexical_inputs[0].relative_path == PurePath("a.pdf")
    assert corpus.lexical_inputs[0].text == "white powdery deposit on stone surface"
    assert metadata_rows[0].pages == (
        CorpusPageText(page_number=2, text="white powdery deposit on stone surface"),
    )


def test_build_document_corpus_strips_lone_surrogates_from_extracted_text(
    tmp_path: Path,
) -> None:
    # Given: extracted page text containing a lone UTF-16 surrogate, a real
    # pdfminer quirk on some fonts/encodings that otherwise breaks
    # downstream hashing and tokenization once it reaches chunk text.
    source_root = tmp_path / "document"
    source_root.mkdir()
    _ = (source_root / "a.pdf").write_bytes(b"%PDF fixture")
    extractor = FakeExtractor(
        {"a.pdf": (CorpusPageText(page_number=1, text="broken \ud83d glyph"),)}
    )

    # When: RAG builds corpus metadata from the source tree.
    metadata_rows = build_document_corpus(
        DocumentCorpusConfig(source_root=source_root),
        extractor,
    )

    # Then: the shared normalizer strips the surrogate to valid Unicode.
    text = metadata_rows[0].text or ""
    assert "\ud83d" not in text
    assert text.encode("utf-8").decode("utf-8") == text


def test_build_document_corpus_excludes_text_garbled_by_font_encoding_mismatch(
    tmp_path: Path,
) -> None:
    # Given: extracted text dominated by Mac OS Roman high-byte characters, the
    # signature of a PDF font encoding pdfminer could not resolve to Unicode
    # (real example: "¯¯ø·«∂˝°«—∏∏≥Æ..." from a symposium proceedings PDF).
    source_root = tmp_path / "document"
    source_root.mkdir()
    _ = (source_root / "a.pdf").write_bytes(b"%PDF fixture")
    garbled_text = bytes(range(0x80, 0xA0)).decode("mac_roman") * 5
    extractor = FakeExtractor(
        {"a.pdf": (CorpusPageText(page_number=1, text=garbled_text),)}
    )

    # When: RAG builds corpus metadata from the source tree.
    metadata_rows = build_document_corpus(
        DocumentCorpusConfig(source_root=source_root),
        extractor,
    )

    # Then: the document is excluded rather than indexed with wrong text.
    assert metadata_rows[0].status == CorpusDocumentStatus.EXCLUDED_GARBLED_TEXT.value
    assert metadata_rows[0].text is None
    assert metadata_rows[0].pages == ()


def test_build_document_corpus_keeps_text_with_occasional_symbol_characters(
    tmp_path: Path,
) -> None:
    # Given: legitimate technical text using a few symbol characters that also
    # appear in the Mac Roman high-byte range (e.g. species names, degree
    # signs) but far below the density a genuinely garbled document reaches.
    source_root = tmp_path / "document"
    source_root.mkdir()
    _ = (source_root / "a.pdf").write_bytes(b"%PDF fixture")
    technical_text = (
        "Bracteacoccus sp. was observed at 24°C on the stone surface, "
        "consistent with prior biological damage surveys of the site."
    )
    extractor = FakeExtractor(
        {"a.pdf": (CorpusPageText(page_number=1, text=technical_text),)}
    )

    # When: RAG builds corpus metadata from the source tree.
    metadata_rows = build_document_corpus(
        DocumentCorpusConfig(source_root=source_root),
        extractor,
    )

    # Then: the document remains indexable.
    assert metadata_rows[0].status == CorpusDocumentStatus.INCLUDED_TEXT_PDF.value
    assert metadata_rows[0].text == technical_text


def test_discover_document_pdfs_rejects_symlinked_pdf_leaf(tmp_path: Path) -> None:
    # Given: a PDF-looking source entry is a symlink outside the corpus root.
    source_root = tmp_path / "document"
    source_root.mkdir()
    outside_pdf = tmp_path / "outside.pdf"
    _ = outside_pdf.write_bytes(b"%PDF outside")
    (source_root / "linked.pdf").symlink_to(outside_pdf)

    # When/Then: discovery rejects the symlink before extraction can read it.
    with pytest.raises(PathSafetyError):
        _ = discover_document_pdfs(source_root)


def test_pdf_text_extractor_recovers_nested_page_text_from_real_source() -> None:
    # Given: a source PDF whose page text is nested below direct layout children.
    pdf_path = SOURCE_DOCUMENT_ROOT / ("한국 도자기 문양의 특성과 상징성 연구.pdf")

    # When: the default pdfminer page extractor reads the file.
    pages = PdfTextExtractor().extract_pages(pdf_path)

    # Then: page-level text is preserved instead of dropping the document.
    assert pages
    assert pages[0].page_number == 1
    assert pages[0].text.strip()


def test_corpus_metadata_records_are_jsonl_ready(tmp_path: Path) -> None:
    # Given: corpus metadata rows from extracted text.
    rows = build_document_corpus(
        DocumentCorpusConfig(source_root=tmp_path),
        FakeExtractor({}),
    )

    # When: records are converted for deterministic materialization.
    records = corpus_metadata_records(rows)

    # Then: empty rows remain JSONL-compatible and deterministic.
    assert records == ()


def test_corpus_metadata_records_include_page_text(tmp_path: Path) -> None:
    # Given: one extracted PDF page with normalized text.
    source_root = tmp_path / "document"
    source_root.mkdir()
    _ = (source_root / "a.pdf").write_bytes(b"%PDF fixture")
    extractor = FakeExtractor(
        {"a.pdf": (CorpusPageText(page_number=3, text="page text remains"),)}
    )

    # When: corpus metadata is materialized as JSONL-ready records.
    rows = build_document_corpus(DocumentCorpusConfig(source_root), extractor)
    records = corpus_metadata_records(rows)

    # Then: page number and page content are preserved with document metadata.
    assert records[0]["pages"] == ({"page_number": 3, "text": "page text remains"},)


def test_extraction_cache_target_uses_existing_safe_cache_boundary(
    tmp_path: Path,
) -> None:
    # Given: a cache fallback outside the protected source document root.
    source_root = tmp_path / "document"
    cache_root = tmp_path / "run-output" / "rag-cache"
    source_root.mkdir()
    cache_root.mkdir(parents=True)
    config = DocumentCorpusConfig(
        source_root=source_root,
        extraction_cache=CacheFallback(
            allowed_root=tmp_path / "run-output",
            root=cache_root,
            relative_path=PurePath("document_corpus/extracted_pages.jsonl"),
        ),
    )

    # When: RAG resolves the optional extraction cache target.
    target = resolve_extraction_cache_target(config)

    # Then: the safe cache helper decides the final path outside source documents.
    assert target == (cache_root / "document_corpus/extracted_pages.jsonl").resolve()


def test_build_document_corpus_keeps_partial_extraction_cache_when_interrupted(
    tmp_path: Path,
) -> None:
    # Given: local PDFs and an extraction cache outside the source tree.
    source_root = tmp_path / "document"
    _write_source_pdfs(source_root, ("a.pdf", "b.pdf"))
    config = DocumentCorpusConfig(
        source_root=source_root,
        extraction_cache=_extraction_cache_fallback(tmp_path),
    )
    extractor = FailingExtractor(
        {"a.pdf": (CorpusPageText(page_number=1, text="cached page text"),)},
        "b.pdf",
    )

    # When: extraction is interrupted after one complete PDF.
    with pytest.raises(
        InterruptedExtractionError,
        match=INTERRUPTED_EXTRACTION_MESSAGE,
    ):
        _ = build_document_corpus(config, extractor)

    # Then: only the completed PDF extraction record is reusable.
    cache_path = resolve_extraction_cache_target(config)
    assert cache_path is not None
    lines = cache_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = parse_json_object(lines[0])
    assert record["relative_path"] == "a.pdf"


def test_build_document_corpus_resumes_from_extraction_cache(
    tmp_path: Path,
) -> None:
    # Given: one source PDF already has complete cached page text.
    source_root = tmp_path / "document"
    _write_source_pdfs(source_root, ("a.pdf", "b.pdf"))
    config = DocumentCorpusConfig(
        source_root=source_root,
        extraction_cache=_extraction_cache_fallback(tmp_path),
    )
    cache_path = resolve_extraction_cache_target(config)
    assert cache_path is not None
    cache_path.parent.mkdir(parents=True)
    _write_extraction_cache(cache_path, "a.pdf", "cached a")
    extractor = RecordingExtractor(
        {"b.pdf": (CorpusPageText(page_number=1, text="fresh b"),)},
        [],
    )

    # When: the corpus build runs again.
    rows = build_document_corpus(config, extractor)

    # Then: cached PDFs are not re-extracted and final rows remain complete.
    assert extractor.calls == ["b.pdf"]
    assert tuple(row.relative_path for row in rows) == ("a.pdf", "b.pdf")
    assert tuple(row.text for row in rows) == ("cached a", "fresh b")


def _write_extraction_cache(cache_path: Path, relative_path: str, text: str) -> None:
    row = {
        "pages": [{"page_number": 1, "text": text}],
        "relative_path": relative_path,
    }
    _ = cache_path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")


def test_default_pdf_text_extractor_uses_pdfminer_dependency() -> None:
    # Given: the default extractor type.
    # When/Then: the project dependency is importable for real corpus builds.
    assert PdfTextExtractor.__module__ == "modules.rag.corpus.document_corpus"
