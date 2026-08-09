from __future__ import annotations

import json
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

import pytest

from modules.rag.corpus.corpus import (
    CorpusDocumentStatus,
    CorpusMetadataRow,
    CorpusPageText,
)
from modules.rag.startup_corpus_cache import startup_corpus_rows
from modules.shared import ContractValidationError, PathSafetyError

if TYPE_CHECKING:
    import os

    from modules.rag.corpus.document_corpus import (
        DocumentCorpusConfig,
        DocumentTextExtractor,
    )


def _corpus_row() -> CorpusMetadataRow:
    return CorpusMetadataRow(
        document_id="stone-conservation",
        relative_path="stone.pdf",
        status=CorpusDocumentStatus.INCLUDED_TEXT_PDF.value,
        text="White powdery deposit on the stone surface.",
        pages=(
            CorpusPageText(
                page_number=1,
                text="White powdery deposit on the stone surface.",
            ),
        ),
    )


def _write_source_pdf(
    source_root: Path, name: str, content: bytes = b"pdf-bytes"
) -> None:
    source_root.mkdir(parents=True, exist_ok=True)
    _ = (source_root / name).write_bytes(content)


def _write_cached_corpus(model_cache_root: Path) -> None:
    cache_path = model_cache_root / "rag" / "document_corpus_metadata.jsonl"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "document_id": "stone-conservation",
        "pages": [
            {"page_number": 1, "text": "White powdery deposit on the stone surface."}
        ],
        "relative_path": "stone.pdf",
        "status": CorpusDocumentStatus.INCLUDED_TEXT_PDF.value,
        "text": "White powdery deposit on the stone surface.",
    }
    _ = cache_path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")


def test_startup_corpus_rows_rebuilds_when_metadata_cache_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: cached source-backed corpus metadata exists from an earlier run.
    monkeypatch.setenv("VCA_DOCUMENT_CORPUS_DIR", "unused-in-tests")
    model_cache_root = tmp_path / "models"
    _write_cached_corpus(model_cache_root)
    calls = 0

    def build_corpus(
        config: DocumentCorpusConfig,
        extractor: DocumentTextExtractor,
    ) -> tuple[CorpusMetadataRow, ...]:
        nonlocal calls
        _ = config, extractor
        calls += 1
        return (_corpus_row(),)

    monkeypatch.setattr(
        "modules.rag.startup_corpus_cache.build_document_corpus",
        build_corpus,
    )

    # When: startup asks for corpus rows.
    rows = startup_corpus_rows(model_cache_root)

    # Then: rows are rebuilt from source instead of read from metadata cache.
    assert rows == (_corpus_row(),)
    assert calls == 1


def test_startup_corpus_rows_writes_cache_after_local_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: no corpus cache exists but local extraction returns source-backed rows.
    monkeypatch.setenv("VCA_DOCUMENT_CORPUS_DIR", "unused-in-tests")
    model_cache_root = tmp_path / "models"

    def build_corpus(
        config: DocumentCorpusConfig,
        extractor: DocumentTextExtractor,
    ) -> tuple[CorpusMetadataRow, ...]:
        _ = config, extractor
        return (_corpus_row(),)

    monkeypatch.setattr(
        "modules.rag.startup_corpus_cache.build_document_corpus",
        build_corpus,
    )

    # When: startup builds corpus rows once.
    rows = startup_corpus_rows(model_cache_root)

    # Then: the source-backed rows are returned and cached for future runs.
    cache_path = model_cache_root / "rag" / "document_corpus_metadata.jsonl"
    assert rows == (_corpus_row(),)
    assert cache_path.is_file()


def test_startup_corpus_rows_rebuilds_empty_metadata_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a previous Docker run left an empty final metadata cache.
    monkeypatch.setenv("VCA_DOCUMENT_CORPUS_DIR", "unused-in-tests")
    model_cache_root = tmp_path / "models"
    cache_path = model_cache_root / "rag" / "document_corpus_metadata.jsonl"
    cache_path.parent.mkdir(parents=True)
    _ = cache_path.write_text("", encoding="utf-8")

    def build_corpus(
        config: DocumentCorpusConfig,
        extractor: DocumentTextExtractor,
    ) -> tuple[CorpusMetadataRow, ...]:
        _ = config, extractor
        return (_corpus_row(),)

    monkeypatch.setattr(
        "modules.rag.startup_corpus_cache.build_document_corpus",
        build_corpus,
    )

    # When: startup asks for corpus rows.
    rows = startup_corpus_rows(model_cache_root)

    # Then: empty metadata is not treated as a completed corpus cache.
    assert rows == (_corpus_row(),)
    assert cache_path.read_text(encoding="utf-8").strip()


def test_startup_corpus_rows_uses_configured_document_source_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Docker exposes the host PDF corpus at a configured container path.
    model_cache_root = tmp_path / "models"
    source_root = tmp_path / "document-corpus"
    monkeypatch.setenv("VCA_DOCUMENT_CORPUS_DIR", str(source_root))
    seen_sources: list[Path] = []

    def build_corpus(
        config: DocumentCorpusConfig,
        extractor: DocumentTextExtractor,
    ) -> tuple[CorpusMetadataRow, ...]:
        _ = extractor
        seen_sources.append(config.source_root)
        return (_corpus_row(),)

    monkeypatch.setattr(
        "modules.rag.startup_corpus_cache.build_document_corpus",
        build_corpus,
    )

    # When: startup builds corpus rows.
    rows = startup_corpus_rows(model_cache_root)

    # Then: the configured container corpus root drives the source adapter.
    assert rows == (_corpus_row(),)
    assert seen_sources == [source_root]


def test_startup_corpus_rows_reuses_cache_when_source_pdfs_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a source PDF set that will not change between two calls.
    model_cache_root = tmp_path / "models"
    source_root = tmp_path / "document-corpus"
    monkeypatch.setenv("VCA_DOCUMENT_CORPUS_DIR", str(source_root))
    _write_source_pdf(source_root, "a.pdf")
    calls = 0

    def build_corpus(
        config: DocumentCorpusConfig,
        extractor: DocumentTextExtractor,
    ) -> tuple[CorpusMetadataRow, ...]:
        nonlocal calls
        _ = config, extractor
        calls += 1
        return (_corpus_row(),)

    monkeypatch.setattr(
        "modules.rag.startup_corpus_cache.build_document_corpus",
        build_corpus,
    )

    # When: startup builds corpus rows twice in a row.
    first_rows = startup_corpus_rows(model_cache_root)
    second_rows = startup_corpus_rows(model_cache_root)

    # Then: the second call reuses the cached rows without rebuilding.
    assert first_rows == (_corpus_row(),)
    assert second_rows == (_corpus_row(),)
    assert calls == 1


def test_startup_corpus_rows_rebuilds_when_source_pdf_is_added(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a cached build exists for one source PDF.
    model_cache_root = tmp_path / "models"
    source_root = tmp_path / "document-corpus"
    monkeypatch.setenv("VCA_DOCUMENT_CORPUS_DIR", str(source_root))
    _write_source_pdf(source_root, "a.pdf")
    calls = 0

    def build_corpus(
        config: DocumentCorpusConfig,
        extractor: DocumentTextExtractor,
    ) -> tuple[CorpusMetadataRow, ...]:
        nonlocal calls
        _ = config, extractor
        calls += 1
        return (_corpus_row(),)

    monkeypatch.setattr(
        "modules.rag.startup_corpus_cache.build_document_corpus",
        build_corpus,
    )
    _ = startup_corpus_rows(model_cache_root)

    # When: a second PDF is added to the source directory before the next call.
    _write_source_pdf(source_root, "b.pdf")
    _ = startup_corpus_rows(model_cache_root)

    # Then: the changed source set forces a full rebuild.
    assert calls == 2


def test_startup_corpus_rows_rebuilds_when_source_pdf_content_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a cached build exists for one source PDF's original content.
    model_cache_root = tmp_path / "models"
    source_root = tmp_path / "document-corpus"
    monkeypatch.setenv("VCA_DOCUMENT_CORPUS_DIR", str(source_root))
    _write_source_pdf(source_root, "a.pdf", content=b"version-one")
    calls = 0

    def build_corpus(
        config: DocumentCorpusConfig,
        extractor: DocumentTextExtractor,
    ) -> tuple[CorpusMetadataRow, ...]:
        nonlocal calls
        _ = config, extractor
        calls += 1
        return (_corpus_row(),)

    monkeypatch.setattr(
        "modules.rag.startup_corpus_cache.build_document_corpus",
        build_corpus,
    )
    _ = startup_corpus_rows(model_cache_root)

    # When: the same-named PDF is replaced in place with different content.
    _write_source_pdf(source_root, "a.pdf", content=b"version-two-is-longer")
    _ = startup_corpus_rows(model_cache_root)

    # Then: the size change is detected and the corpus is rebuilt, not served stale.
    assert calls == 2


def test_startup_corpus_rows_cache_round_trips_excluded_no_ocr_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a scanned PDF with no extractable text.
    model_cache_root = tmp_path / "models"
    source_root = tmp_path / "document-corpus"
    monkeypatch.setenv("VCA_DOCUMENT_CORPUS_DIR", str(source_root))
    _write_source_pdf(source_root, "scan.pdf")
    excluded_row = CorpusMetadataRow(
        document_id="scan",
        relative_path="scan.pdf",
        status=CorpusDocumentStatus.EXCLUDED_NO_OCR.value,
        text=None,
        pages=(),
    )

    def build_corpus(
        config: DocumentCorpusConfig,
        extractor: DocumentTextExtractor,
    ) -> tuple[CorpusMetadataRow, ...]:
        _ = config, extractor
        return (excluded_row,)

    monkeypatch.setattr(
        "modules.rag.startup_corpus_cache.build_document_corpus",
        build_corpus,
    )

    # When: startup builds corpus rows twice, the second time from cache.
    first_rows = startup_corpus_rows(model_cache_root)
    second_rows = startup_corpus_rows(model_cache_root)

    # Then: the null-text, no-page row round-trips through the cache exactly.
    assert first_rows == (excluded_row,)
    assert second_rows == (excluded_row,)


def test_startup_corpus_rows_rebuilds_when_cached_metadata_is_malformed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a fresh cache whose metadata file is later corrupted on disk.
    model_cache_root = tmp_path / "models"
    source_root = tmp_path / "document-corpus"
    monkeypatch.setenv("VCA_DOCUMENT_CORPUS_DIR", str(source_root))
    _write_source_pdf(source_root, "a.pdf")
    calls = 0

    def build_corpus(
        config: DocumentCorpusConfig,
        extractor: DocumentTextExtractor,
    ) -> tuple[CorpusMetadataRow, ...]:
        nonlocal calls
        _ = config, extractor
        calls += 1
        return (_corpus_row(),)

    monkeypatch.setattr(
        "modules.rag.startup_corpus_cache.build_document_corpus",
        build_corpus,
    )
    _ = startup_corpus_rows(model_cache_root)
    cache_path = model_cache_root / "rag" / "document_corpus_metadata.jsonl"
    _ = cache_path.write_text("not json\n", encoding="utf-8")

    # When: startup asks for corpus rows again with a matching fingerprint.
    rows = startup_corpus_rows(model_cache_root)

    # Then: the unreadable cache is treated as a miss rather than propagating.
    assert rows == (_corpus_row(),)
    assert calls == 2


def test_startup_corpus_rows_rejects_blank_document_source_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the document corpus env is present but blank.
    monkeypatch.setenv("VCA_DOCUMENT_CORPUS_DIR", " ")

    # When/Then: startup rejects the invalid boundary value.
    with pytest.raises(ContractValidationError, match="VCA_DOCUMENT_CORPUS_DIR"):
        _ = startup_corpus_rows(tmp_path / "models")


def test_startup_corpus_rows_does_not_treat_partial_extraction_cache_as_final(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: only the resumable extraction sidecar exists, not the final corpus cache.
    monkeypatch.setenv("VCA_DOCUMENT_CORPUS_DIR", "unused-in-tests")
    model_cache_root = tmp_path / "models"
    extraction_cache = (
        model_cache_root / "rag" / "document_corpus_extracted_pages.jsonl"
    )
    extraction_cache.parent.mkdir(parents=True)
    row = {
        "pages": [{"page_number": 1, "text": "cached a"}],
        "relative_path": "a.pdf",
    }
    _ = extraction_cache.write_text(
        json.dumps(row, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    calls: list[DocumentCorpusConfig] = []

    def build_corpus(
        config: DocumentCorpusConfig,
        extractor: DocumentTextExtractor,
    ) -> tuple[CorpusMetadataRow, ...]:
        _ = extractor
        calls.append(config)
        return (_corpus_row(),)

    monkeypatch.setattr(
        "modules.rag.startup_corpus_cache.build_document_corpus",
        build_corpus,
    )

    # When: startup asks for corpus rows with no complete metadata cache.
    rows = startup_corpus_rows(model_cache_root)

    # Then: startup builds through the source-backed corpus path without cache input.
    assert rows == (_corpus_row(),)
    assert len(calls) == 1
    assert calls[0].extraction_cache is None


def test_startup_corpus_rows_rejects_symlinked_metadata_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the metadata cache temp leaf is a symlink to an external file.
    monkeypatch.setenv("VCA_DOCUMENT_CORPUS_DIR", "unused-in-tests")
    model_cache_root = tmp_path / "models"
    temp_path = model_cache_root / "rag" / "document_corpus_metadata.jsonl.tmp"
    temp_path.parent.mkdir(parents=True)
    external_file = tmp_path / "external-metadata.jsonl"
    _ = external_file.write_text("sentinel\n", encoding="utf-8")
    temp_path.symlink_to(external_file)

    def build_corpus(
        config: DocumentCorpusConfig,
        extractor: DocumentTextExtractor,
    ) -> tuple[CorpusMetadataRow, ...]:
        _ = config, extractor
        return (_corpus_row(),)

    monkeypatch.setattr(
        "modules.rag.startup_corpus_cache.build_document_corpus",
        build_corpus,
    )

    # When/Then: startup refuses to follow the symlinked temp leaf.
    with pytest.raises(PathSafetyError):
        _ = startup_corpus_rows(model_cache_root)
    assert external_file.read_text(encoding="utf-8") == "sentinel\n"


def test_startup_corpus_rows_rejects_symlinked_model_parent_before_mkdir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the model cache parent is a symlink to an external directory.
    linked_parent = tmp_path / "linked-models"
    external_parent = tmp_path / "external-models"
    external_parent.mkdir()
    linked_parent.symlink_to(external_parent, target_is_directory=True)
    model_cache_root = linked_parent / "models"

    def build_corpus(
        config: DocumentCorpusConfig,
        extractor: DocumentTextExtractor,
    ) -> tuple[CorpusMetadataRow, ...]:
        _ = config, extractor
        return (_corpus_row(),)

    monkeypatch.setattr(
        "modules.rag.startup_corpus_cache.build_document_corpus",
        build_corpus,
    )

    # When/Then: startup refuses before creating cache dirs through the symlink.
    with pytest.raises(PathSafetyError):
        _ = startup_corpus_rows(model_cache_root)
    assert not (external_parent / "models").exists()


def test_startup_corpus_rows_fingerprints_relative_paths_with_lone_surrogates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a discovered relative path containing a lone UTF-16 surrogate.
    # Real filenames can decode to this under POSIX surrogateescape
    # handling (macOS/APFS rejects creating such a file directly, so the
    # discovered path and its stat() are faked here instead of using a
    # real file on disk).
    model_cache_root = tmp_path / "models"
    source_root = tmp_path / "document-corpus"
    source_root.mkdir(parents=True)
    monkeypatch.setenv("VCA_DOCUMENT_CORPUS_DIR", str(source_root))
    bad_relative_path = PurePath("broken-\udcff-name.pdf")
    # A real, validly-named stand-in file with a stable stat() result: the
    # bad path can't exist on disk (APFS rejects it), and stat()-ing
    # anything under tmp_path would pick up mtime churn from the cache
    # writes this test triggers.
    stat_stand_in = source_root / "stand-in.pdf"
    _ = stat_stand_in.write_bytes(b"pdf-bytes")
    real_stat = Path.stat

    def fake_stat(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        if self.name == bad_relative_path.name:
            return real_stat(stat_stand_in)
        return real_stat(self, *args, **kwargs)

    def fake_discover(source_root: Path) -> tuple[PurePath, ...]:
        _ = source_root
        return (bad_relative_path,)

    monkeypatch.setattr(
        "modules.rag.startup_corpus_cache.discover_document_pdfs",
        fake_discover,
    )
    monkeypatch.setattr(Path, "stat", fake_stat)
    calls = 0

    def build_corpus(
        config: DocumentCorpusConfig,
        extractor: DocumentTextExtractor,
    ) -> tuple[CorpusMetadataRow, ...]:
        nonlocal calls
        _ = config, extractor
        calls += 1
        return (_corpus_row(),)

    monkeypatch.setattr(
        "modules.rag.startup_corpus_cache.build_document_corpus",
        build_corpus,
    )

    # When: startup fingerprints the source directory twice without changes.
    first_rows = startup_corpus_rows(model_cache_root)
    second_rows = startup_corpus_rows(model_cache_root)

    # Then: fingerprinting does not raise and the cache is reused.
    assert first_rows == (_corpus_row(),)
    assert second_rows == (_corpus_row(),)
    assert calls == 1
