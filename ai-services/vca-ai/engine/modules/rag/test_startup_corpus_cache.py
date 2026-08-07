from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from modules.rag.corpus.corpus import CorpusMetadataRow, CorpusPageText
from modules.rag.startup_corpus_cache import startup_corpus_rows
from modules.shared import PathSafetyError

if TYPE_CHECKING:
    from pathlib import Path

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
    assert calls == [("first.pdf",), ("first.pdf",), ("first.pdf", "second.pdf")]


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


def test_startup_corpus_rows_rejects_symlinked_metadata_temp_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: the metadata cache temp leaf is a symlink to an external file.
    model_cache_root = tmp_path / "models"
    temp_path = model_cache_root / "rag" / "document_corpus_metadata.jsonl.tmp"
    temp_path.parent.mkdir(parents=True)
    external_file = tmp_path / "external-metadata.jsonl"
    _ = external_file.write_text("sentinel\n", encoding="utf-8")
    temp_path.symlink_to(external_file)

    def build_rows(
        _config: DocumentCorpusConfig,
        _extractor: object,
    ) -> tuple[CorpusMetadataRow, ...]:
        return (_row("first.pdf"),)

    monkeypatch.setattr(
        "modules.rag.startup_corpus_cache.build_document_corpus",
        build_rows,
    )

    # When/Then: startup refuses to follow the symlinked temp leaf.
    with pytest.raises(PathSafetyError):
        _ = startup_corpus_rows(model_cache_root)
    assert external_file.read_text(encoding="utf-8") == "sentinel\n"


def test_startup_corpus_rows_rejects_symlinked_model_parent_before_mkdir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: the model cache parent is a symlink to an external directory.
    linked_parent = tmp_path / "linked-models"
    external_parent = tmp_path / "external-models"
    external_parent.mkdir()
    linked_parent.symlink_to(external_parent, target_is_directory=True)
    model_cache_root = linked_parent / "models"

    def build_rows(
        _config: DocumentCorpusConfig,
        _extractor: object,
    ) -> tuple[CorpusMetadataRow, ...]:
        return (_row("first.pdf"),)

    monkeypatch.setattr(
        "modules.rag.startup_corpus_cache.build_document_corpus",
        build_rows,
    )

    # When/Then: startup refuses before creating cache dirs through the symlink.
    with pytest.raises(PathSafetyError):
        _ = startup_corpus_rows(model_cache_root)
    assert not (external_parent / "models").exists()


def _row(relative_path: str, text: str = "text") -> CorpusMetadataRow:
    return CorpusMetadataRow(
        document_id=relative_path.removesuffix(".pdf"),
        relative_path=relative_path,
        status="included_text_pdf",
        text=text,
        pages=(CorpusPageText(page_number=1, text=text),),
    )
