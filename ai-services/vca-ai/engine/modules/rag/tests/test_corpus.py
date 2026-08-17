from __future__ import annotations

from pathlib import Path, PurePath

import pytest

from modules.rag.corpus.cache import CacheFallback, safe_cache_target
from modules.rag.corpus.corpus import (
    Corpus,
    CorpusDocumentId,
    CorpusDocumentStatus,
    CorpusMetadataRow,
    CorpusPageText,
    CorpusRecord,
    parse_corpus_record,
)
from modules.shared import ContractValidationError, PathSafetyError


def _synthetic_metadata_rows() -> tuple[CorpusMetadataRow, ...]:
    included_rows = tuple(
        CorpusMetadataRow(
            document_id=f"text-pdf-{index:03d}",
            relative_path=f"pdfs/text-{index:03d}.pdf",
            status=CorpusDocumentStatus.INCLUDED_TEXT_PDF.value,
            text=f"indexed text {index}",
        )
        for index in range(253)
    )
    excluded_rows = tuple(
        CorpusMetadataRow(
            document_id=f"scanned-pdf-{index:03d}",
            relative_path=f"pdfs/scanned-{index:03d}.pdf",
            status=CorpusDocumentStatus.EXCLUDED_NO_OCR.value,
            text=None,
        )
        for index in range(7)
    )
    return included_rows + excluded_rows


def test_corpus_accounting_and_lexical_inputs_are_deterministic() -> None:
    # Given: synthetic metadata for text PDFs and scanned PDFs with no OCR text.
    metadata_rows = _synthetic_metadata_rows()

    # When: the metadata is parsed into a corpus.
    corpus = Corpus.from_metadata(metadata_rows)

    # Then: accounting and lexical inputs contain only included PDFs in source order.
    assert corpus.accounting.attempted_pdfs == 260
    assert corpus.accounting.included_text_pdfs == 253
    assert corpus.accounting.excluded_no_ocr == 7
    assert tuple(item.document_id for item in corpus.lexical_inputs) == tuple(
        f"text-pdf-{index:03d}" for index in range(253)
    )


def test_parse_corpus_record_rejects_non_pdf_metadata() -> None:
    # Given: metadata whose relative path is not a PDF.
    malformed_row = CorpusMetadataRow(
        document_id="not-a-pdf",
        relative_path="pdfs/not-a-pdf.txt",
        status=CorpusDocumentStatus.INCLUDED_TEXT_PDF.value,
        text="text",
    )

    # When: the metadata is parsed at the corpus boundary.
    with pytest.raises(ContractValidationError) as error:
        _ = parse_corpus_record(malformed_row)

    # Then: a malformed corpus row cannot be constructed.
    assert error.value.field == "relative_path"


def test_parse_corpus_record_rejects_no_ocr_rows_with_text() -> None:
    # Given: a no-OCR status paired with unexpected extracted text.
    inconsistent_row = CorpusMetadataRow(
        document_id="scanned-pdf",
        relative_path="pdfs/scanned.pdf",
        status=CorpusDocumentStatus.EXCLUDED_NO_OCR.value,
        text="unexpected OCR text",
    )

    # When: the metadata is parsed at the corpus boundary.
    with pytest.raises(ContractValidationError) as error:
        _ = parse_corpus_record(inconsistent_row)

    # Then: an inconsistent corpus row cannot be constructed.
    assert error.value.field == "text"


def test_parse_corpus_record_rejects_no_ocr_rows_with_pages() -> None:
    # Given: a no-OCR status paired with extracted page text.
    inconsistent_row = CorpusMetadataRow(
        document_id="scanned-pdf",
        relative_path="pdfs/scanned.pdf",
        status=CorpusDocumentStatus.EXCLUDED_NO_OCR.value,
        text=None,
        pages=(CorpusPageText(page_number=1, text="unexpected text"),),
    )

    # When: the metadata is parsed at the corpus boundary.
    with pytest.raises(ContractValidationError) as error:
        _ = parse_corpus_record(inconsistent_row)

    # Then: excluded rows cannot retain page-level text.
    assert error.value.field == "pages"


def test_parse_corpus_record_rejects_garbled_text_rows_with_text() -> None:
    # Given: a garbled-text status paired with unexpected retained text.
    inconsistent_row = CorpusMetadataRow(
        document_id="garbled-pdf",
        relative_path="pdfs/garbled.pdf",
        status=CorpusDocumentStatus.EXCLUDED_GARBLED_TEXT.value,
        text="unexpected garbled text",
    )

    # When: the metadata is parsed at the corpus boundary.
    with pytest.raises(ContractValidationError) as error:
        _ = parse_corpus_record(inconsistent_row)

    # Then: an inconsistent corpus row cannot be constructed.
    assert error.value.field == "text"


def test_garbled_text_pdfs_are_excluded_and_counted_separately() -> None:
    # Given: one indexable PDF and one PDF excluded for font-encoding garble.
    metadata_rows = (
        CorpusMetadataRow(
            document_id="text-pdf-001",
            relative_path="pdfs/text-001.pdf",
            status=CorpusDocumentStatus.INCLUDED_TEXT_PDF.value,
            text="indexed text",
        ),
        CorpusMetadataRow(
            document_id="garbled-pdf-001",
            relative_path="pdfs/garbled-001.pdf",
            status=CorpusDocumentStatus.EXCLUDED_GARBLED_TEXT.value,
            text=None,
        ),
    )

    # When: the metadata is parsed into a corpus.
    corpus = Corpus.from_metadata(metadata_rows)

    # Then: the garbled PDF is excluded from indexing and accounted separately
    # from no-OCR exclusions.
    assert corpus.accounting.attempted_pdfs == 2
    assert corpus.accounting.included_text_pdfs == 1
    assert corpus.accounting.excluded_no_ocr == 0
    assert corpus.accounting.excluded_garbled_text == 1
    assert tuple(item.document_id for item in corpus.lexical_inputs) == (
        "text-pdf-001",
    )


def test_corpus_rejects_duplicate_document_ids() -> None:
    # Given: two validated records with the same document identifier.
    record = CorpusRecord(
        document_id=CorpusDocumentId("duplicate-pdf"),
        relative_path=PurePath("pdfs/duplicate.pdf"),
        status=CorpusDocumentStatus.INCLUDED_TEXT_PDF,
        text="indexed text",
    )

    # When: the records form one corpus.
    with pytest.raises(ContractValidationError) as error:
        _ = Corpus((record, record))

    # Then: duplicate corpus rows cannot be constructed.
    assert error.value.field == "records"


def test_safe_cache_target_uses_fallback_and_rejects_source_document_targets(
    tmp_path: Path,
) -> None:
    # Given: a synthetic stand-in for the protected source document root.
    source_document_root = tmp_path / "Downloads/dataset/document"
    source_document_root.mkdir(parents=True)
    fallback = CacheFallback(
        allowed_root=tmp_path / "run-output",
        root=tmp_path / "run-output/rag-cache",
        relative_path=PurePath("corpus/index.json"),
    )
    source_root_fallback = CacheFallback(
        allowed_root=tmp_path,
        root=source_document_root,
        relative_path=PurePath("corpus/index.json"),
    )

    # When: safe and source-root cache targets are resolved before writing.
    cache_target = safe_cache_target(source_document_root, fallback)

    # Then: the fallback is outside source documents and source-root writes fail closed.
    assert (
        cache_target == (tmp_path / "run-output/rag-cache/corpus/index.json").resolve()
    )
    with pytest.raises(PathSafetyError) as error:
        _ = safe_cache_target(source_document_root, source_root_fallback)
    assert error.value.path == str(
        source_root_fallback.root / source_root_fallback.relative_path
    )


def test_safe_cache_target_rejects_escape_from_allowed_fallback_root(
    tmp_path: Path,
) -> None:
    # Given: a fallback root outside the caller-declared run-output root.
    source_document_root = tmp_path / "Downloads/dataset/document"
    allowed_root = tmp_path / "run-output"
    source_document_root.mkdir(parents=True)
    allowed_root.mkdir()
    outside_fallback = CacheFallback(
        allowed_root=allowed_root,
        root=tmp_path / "unexpected-cache",
        relative_path=PurePath("corpus/index.json"),
    )

    # When: the cache target is resolved before writing.
    with pytest.raises(PathSafetyError) as error:
        _ = safe_cache_target(source_document_root, outside_fallback)

    # Then: only cache roots contained by the allowed root are accepted.
    assert error.value.reason == "cache root escapes allowed root"


def test_safe_cache_target_rejects_symlink_escape_from_cache_root(
    tmp_path: Path,
) -> None:
    # Given: a symlink inside the cache root that points outside the allowed cache tree.
    source_document_root = tmp_path / "Downloads/dataset/document"
    cache_root = tmp_path / "run-output/rag-cache"
    outside_root = tmp_path / "outside"
    source_document_root.mkdir(parents=True)
    cache_root.mkdir(parents=True)
    outside_root.mkdir()
    (cache_root / "link").symlink_to(outside_root, target_is_directory=True)
    fallback = CacheFallback(
        allowed_root=tmp_path / "run-output",
        root=cache_root,
        relative_path=PurePath("link/index.json"),
    )

    # When: the symlinked target is resolved before writing.
    with pytest.raises(PathSafetyError) as error:
        _ = safe_cache_target(source_document_root, fallback)

    # Then: symlink traversal cannot redirect sidecars outside the cache root.
    assert error.value.reason == "cache target escapes fallback root"
