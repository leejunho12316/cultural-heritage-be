"""Startup-safe cache for source-backed document corpus metadata."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Final

from modules.rag.corpus.cache import CacheFallback, safe_cache_target
from modules.rag.corpus.document_corpus import (
    DocumentCorpusConfig,
    PdfTextExtractor,
    build_document_corpus,
    corpus_metadata_records,
)
from modules.shared import (
    ContractValidationError,
    ensure_contained_write_path,
    ensure_no_symlink_leaf,
    ensure_no_symlink_path_components,
    ensure_source_document_is_not_write_target,
)

if TYPE_CHECKING:
    from modules.rag.corpus.corpus import CorpusMetadataRow

CACHE_ROOT_NAME: Final = "rag"
CORPUS_CACHE_NAME: Final = "document_corpus_metadata.jsonl"
DOCUMENT_CORPUS_DIR_ENV: Final = "VCA_DOCUMENT_CORPUS_DIR"


@dataclass(frozen=True, slots=True)
class _WriteBoundary:
    root: Path
    source_root: Path


def startup_corpus_rows(model_cache_root: Path) -> tuple[CorpusMetadataRow, ...]:
    """Build source corpus rows freshly and cache only the new output."""
    _guard_model_cache_root(model_cache_root)
    config = _document_corpus_config(model_cache_root)
    cache_path = _corpus_cache_path(model_cache_root, config)
    rows = build_document_corpus(config, PdfTextExtractor())
    boundary = _WriteBoundary(model_cache_root, config.source_root)
    _write_corpus_cache(cache_path, rows, boundary)
    return rows


def _document_corpus_config(model_cache_root: Path) -> DocumentCorpusConfig:
    _ = model_cache_root
    source_root = _document_source_root()
    return DocumentCorpusConfig(
        source_root=source_root,
        extraction_cache=None,
    )


def _corpus_cache_path(model_cache_root: Path, config: DocumentCorpusConfig) -> Path:
    fallback = CacheFallback(
        allowed_root=model_cache_root,
        root=model_cache_root / CACHE_ROOT_NAME,
        relative_path=PurePath(CORPUS_CACHE_NAME),
    )
    return safe_cache_target(config.source_root, fallback)


def _write_corpus_cache(
    path: Path, rows: tuple[CorpusMetadataRow, ...], boundary: _WriteBoundary
) -> None:
    if not rows:
        field = "document_corpus"
        reason = "source corpus produced no metadata rows"
        raise ContractValidationError(field, reason)
    payload = "" if not rows else "\n".join(
        json.dumps(row, sort_keys=True, separators=(",", ":"))
        for row in corpus_metadata_records(rows)
    ) + "\n"
    _guard_write_directory(path.parent, boundary)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(path, payload, boundary)


def _write_text_atomic(path: Path, payload: str, boundary: _WriteBoundary) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        _guard_write_path(path, boundary)
        _guard_write_path(temporary, boundary)
        _ = temporary.write_text(payload, encoding="utf-8")
        _ = temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _guard_write_path(path: Path, boundary: _WriteBoundary) -> None:
    reason = "startup corpus cache path escapes or uses symlinks"
    _ = ensure_contained_write_path(boundary.root, path, reason)
    _ = ensure_no_symlink_leaf(path, reason)
    _ = ensure_source_document_is_not_write_target(boundary.source_root, path)


def _guard_write_directory(path: Path, boundary: _WriteBoundary) -> None:
    reason = "startup corpus cache directory escapes or uses symlinks"
    _ = ensure_no_symlink_path_components(path, reason)
    _ = ensure_source_document_is_not_write_target(boundary.source_root, path)


def _guard_model_cache_root(path: Path) -> None:
    reason = "startup corpus cache root escapes or uses symlinks"
    _ = ensure_no_symlink_path_components(path, reason)


def startup_document_source_root() -> Path:
    """Return the configured source document root for startup RAG."""
    raw_source_root = os.environ.get(DOCUMENT_CORPUS_DIR_ENV)
    if raw_source_root is None:
        return DocumentCorpusConfig().source_root
    if not raw_source_root.strip():
        field = DOCUMENT_CORPUS_DIR_ENV
        reason = "must not be blank"
        raise ContractValidationError(field, reason)
    return Path(raw_source_root)


def _document_source_root() -> Path:
    return startup_document_source_root()
