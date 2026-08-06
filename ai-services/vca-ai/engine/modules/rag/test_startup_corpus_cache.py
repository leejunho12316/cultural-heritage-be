from __future__ import annotations

from typing import TYPE_CHECKING

from modules.rag.corpus.corpus import CorpusMetadataRow, CorpusPageText
from modules.rag.startup_corpus_cache import startup_corpus_rows

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from modules.rag.corpus.document_corpus import DocumentCorpusConfig


def test_startup_corpus_rows_rebuilds_when_pdf_set_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "document"
    model_cache_root = tmp_path / "models"
    source_root.mkdir()
    model_cache_root.mkdir()
    monkeypatch.setenv("VCA_DOCUMENT_CORPUS_DIR", str(source_root))
    calls: list[tuple[str, ...]] = []

    def build_rows(
        config: DocumentCorpusConfig,
        _extractor: object,
    ) -> tuple[CorpusMetadataRow, ...]:
        paths = tuple(path.name for path in sorted(config.source_root.glob("*.pdf")))
        calls.append(paths)
        return tuple(_row(path) for path in paths)

    monkeypatch.setattr(
        "modules.rag.startup_corpus_cache.build_document_corpus",
        build_rows,
    )
    _ = (source_root / "first.pdf").write_bytes(b"%PDF-1.7\nfirst")

    first_rows = startup_corpus_rows(model_cache_root)
    cached_rows = startup_corpus_rows(model_cache_root)
    _ = (source_root / "second.pdf").write_bytes(b"%PDF-1.7\nsecond")
    rebuilt_rows = startup_corpus_rows(model_cache_root)

    assert tuple(row.relative_path for row in first_rows) == ("first.pdf",)
    assert tuple(row.relative_path for row in cached_rows) == ("first.pdf",)
    assert tuple(row.relative_path for row in rebuilt_rows) == (
        "first.pdf",
        "second.pdf",
    )
    assert calls == [("first.pdf",), ("first.pdf", "second.pdf")]


def test_startup_corpus_rows_drops_extraction_cache_when_source_is_stale(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "document"
    model_cache_root = tmp_path / "models"
    source_root.mkdir()
    model_cache_root.mkdir()
    monkeypatch.setenv("VCA_DOCUMENT_CORPUS_DIR", str(source_root))
    _ = (source_root / "first.pdf").write_bytes(b"%PDF-1.7\nfirst")
    extraction_cache = (
        model_cache_root / "rag" / "document_corpus_extracted_pages.jsonl"
    )
    calls = 0

    def build_rows(
        _config: DocumentCorpusConfig,
        _extractor: object,
    ) -> tuple[CorpusMetadataRow, ...]:
        nonlocal calls
        calls += 1
        extraction_cache.parent.mkdir(parents=True, exist_ok=True)
        _ = extraction_cache.write_text("stale\n", encoding="utf-8")
        return (_row("first.pdf", text=f"version {calls}"),)

    monkeypatch.setattr(
        "modules.rag.startup_corpus_cache.build_document_corpus",
        build_rows,
    )
    first_rows = startup_corpus_rows(model_cache_root)
    _ = (source_root / "first.pdf").write_bytes(b"%PDF-1.7\nupdated")
    second_rows = startup_corpus_rows(model_cache_root)

    assert first_rows[0].text == "version 1"
    assert second_rows[0].text == "version 2"
    assert calls == 2


def _row(relative_path: str, text: str = "text") -> CorpusMetadataRow:
    return CorpusMetadataRow(
        document_id=relative_path.removesuffix(".pdf"),
        relative_path=relative_path,
        status="included_text_pdf",
        text=text,
        pages=(CorpusPageText(page_number=1, text=text),),
    )
