"""Startup-safe cache for source-backed document corpus metadata."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePath
from typing import Final

from modules.rag.corpus.cache import CacheFallback, safe_cache_target
from modules.rag.corpus.corpus import CorpusMetadataRow, CorpusPageText
from modules.rag.corpus.document_corpus import (
    DocumentCorpusConfig,
    PdfTextExtractor,
    build_document_corpus,
    corpus_metadata_records,
    discover_document_pdfs,
    resolve_extraction_cache_target,
)
from modules.shared import ContractValidationError
from modules.shared.json_object import JsonObject, parse_json_object_for_field

CACHE_ROOT_NAME: Final = "rag"
CORPUS_CACHE_NAME: Final = "document_corpus_metadata.jsonl"
EXTRACTION_CACHE_NAME: Final = "document_corpus_extracted_pages.jsonl"
DOCUMENT_CORPUS_DIR_ENV: Final = "VCA_DOCUMENT_CORPUS_DIR"


def startup_corpus_rows(model_cache_root: Path) -> tuple[CorpusMetadataRow, ...]:
    """Return cached corpus rows or build and cache them outside source documents."""
    config = _document_corpus_config(model_cache_root)
    cache_path = _corpus_cache_path(model_cache_root, config)
    cached_rows = _read_corpus_cache(cache_path)
    if cached_rows is not None and _cache_matches_source(
        cache_path,
        config,
        cached_rows,
    ):
        return cached_rows
    _remove_extraction_cache(config)
    rows = build_document_corpus(config, PdfTextExtractor())
    _write_corpus_cache(cache_path, rows)
    return rows


def _document_corpus_config(model_cache_root: Path) -> DocumentCorpusConfig:
    source_root = _document_source_root()
    return DocumentCorpusConfig(
        source_root=source_root,
        extraction_cache=CacheFallback(
            allowed_root=model_cache_root,
            root=model_cache_root / CACHE_ROOT_NAME,
            relative_path=PurePath(EXTRACTION_CACHE_NAME),
        )
    )


def _corpus_cache_path(model_cache_root: Path, config: DocumentCorpusConfig) -> Path:
    fallback = CacheFallback(
        allowed_root=model_cache_root,
        root=model_cache_root / CACHE_ROOT_NAME,
        relative_path=PurePath(CORPUS_CACHE_NAME),
    )
    return safe_cache_target(config.source_root, fallback)


def _read_corpus_cache(path: Path) -> tuple[CorpusMetadataRow, ...] | None:
    if not path.is_file():
        return None
    lines = tuple(
        line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    )
    if not lines:
        return None
    return tuple(
        _metadata_row(parse_json_object_for_field(line, "document_corpus_metadata"))
        for line in lines
    )


def _cache_matches_source(
    cache_path: Path,
    config: DocumentCorpusConfig,
    cached_rows: tuple[CorpusMetadataRow, ...],
) -> bool:
    source_paths = discover_document_pdfs(config.source_root)
    cached_paths = tuple(row.relative_path for row in cached_rows)
    if cached_paths != tuple(str(path) for path in source_paths):
        return False
    cache_modified_ns = cache_path.stat().st_mtime_ns
    return all(
        (config.source_root / relative_path).stat().st_mtime_ns <= cache_modified_ns
        for relative_path in source_paths
    )


def _remove_extraction_cache(config: DocumentCorpusConfig) -> None:
    extraction_cache_path = resolve_extraction_cache_target(config)
    if extraction_cache_path is not None:
        extraction_cache_path.unlink(missing_ok=True)


def _write_corpus_cache(path: Path, rows: tuple[CorpusMetadataRow, ...]) -> None:
    if not rows:
        field = "document_corpus"
        reason = "source corpus produced no metadata rows"
        raise ContractValidationError(field, reason)
    payload = "" if not rows else "\n".join(
        json.dumps(row, sort_keys=True, separators=(",", ":"))
        for row in corpus_metadata_records(rows)
    ) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(path, payload)


def _metadata_row(record: JsonObject) -> CorpusMetadataRow:
    return CorpusMetadataRow(
        document_id=_string(record, "document_id"),
        relative_path=_string(record, "relative_path"),
        status=_string(record, "status"),
        text=_optional_string(record, "text"),
        pages=_page_rows(record),
    )


def _page_rows(record: JsonObject) -> tuple[CorpusPageText, ...]:
    value = record.get("pages")
    if not isinstance(value, list):
        field = "pages"
        reason = "must be an array"
        raise ContractValidationError(field, reason)
    return tuple(_page_row(page) for page in value if isinstance(page, dict))


def _page_row(record: JsonObject) -> CorpusPageText:
    page_number = record.get("page_number")
    if not isinstance(page_number, int) or isinstance(page_number, bool):
        field = "page_number"
        reason = "must be an integer"
        raise ContractValidationError(field, reason)
    return CorpusPageText(page_number=page_number, text=_string(record, "text"))


def _string(record: JsonObject, field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        reason = "must be a non-blank string"
        raise ContractValidationError(field, reason)
    return value


def _optional_string(record: JsonObject, field: str) -> str | None:
    value = record.get(field)
    if value is None or isinstance(value, str):
        return value
    reason = "must be a string or null"
    raise ContractValidationError(field, reason)


def _write_text_atomic(path: Path, payload: str) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        _ = temporary.write_text(payload, encoding="utf-8")
        _ = temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _document_source_root() -> Path:
    raw_source_root = os.environ.get(DOCUMENT_CORPUS_DIR_ENV)
    if raw_source_root is None:
        return DocumentCorpusConfig().source_root
    if not raw_source_root.strip():
        field = DOCUMENT_CORPUS_DIR_ENV
        reason = "must not be blank"
        raise ContractValidationError(field, reason)
    return Path(raw_source_root)
