"""Startup-safe cache for source-backed document corpus metadata."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from hashlib import sha256
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
)
from modules.shared import (
    ContractValidationError,
    ensure_contained_write_path,
    ensure_no_symlink_leaf,
    ensure_no_symlink_path_components,
    ensure_source_document_is_not_write_target,
)
from modules.shared.json_object import (
    JsonObject,
    JsonValue,
    parse_json_object_for_field,
)

CACHE_ROOT_NAME: Final = "rag"
CORPUS_CACHE_NAME: Final = "document_corpus_metadata.jsonl"
CORPUS_FINGERPRINT_NAME: Final = "document_corpus_fingerprint.json"
DOCUMENT_CORPUS_DIR_ENV: Final = "VCA_DOCUMENT_CORPUS_DIR"


@dataclass(frozen=True, slots=True)
class _WriteBoundary:
    root: Path
    source_root: Path


# startup_runner._locked_corpus_vector_index가 락을 잡은 상태에서 호출하는
# 코퍼스 메타데이터 진입점. 캐시 재사용 조건은 아래 docstring 참고.
def startup_corpus_rows(model_cache_root: Path) -> tuple[CorpusMetadataRow, ...]:
    """Reuse a cached corpus build only when the source PDF set is unchanged.

    The source directory is fingerprinted by each PDF's relative path, size,
    and mtime. Any addition, removal, or in-place content change flips the
    fingerprint and forces a full rebuild, so a stale cache can never be
    served silently; the fingerprint check only ever skips work, it never
    changes what the freshly-built corpus would have contained.
    """
    _guard_model_cache_root(model_cache_root)
    config = _document_corpus_config(model_cache_root)
    cache_path = _corpus_cache_path(model_cache_root, config)
    fingerprint_path = _corpus_fingerprint_path(model_cache_root, config)
    boundary = _WriteBoundary(model_cache_root, config.source_root)
    fingerprint = _source_fingerprint(config.source_root)
    cached_rows = _cached_rows_if_fresh(cache_path, fingerprint_path, fingerprint)
    if cached_rows is not None:
        return cached_rows
    rows = build_document_corpus(config, PdfTextExtractor())
    _write_corpus_cache(cache_path, rows, boundary)
    _write_fingerprint(fingerprint_path, fingerprint, boundary)
    return rows


# 소스 디렉터리 전체의 상태를 하나의 해시로 요약한다. startup_corpus_rows가
# 캐시 유효성을 판단하는 기준이 되며, 내용까지는 읽지 않고 경로/크기/mtime만
# 본다(빠르지만 mtime을 보존한 내용 변경은 놓칠 수 있음).
def _source_fingerprint(source_root: Path) -> str:
    # Relative paths can contain lone UTF-16 surrogates on POSIX (pathlib
    # decodes unrepresentable filename bytes with surrogateescape). This
    # hash only needs a stable byte representation, so pass surrogates
    # through instead of raising.
    entries: list[str] = []
    for relative_path in discover_document_pdfs(source_root):
        pdf_stat = (source_root / relative_path).stat()
        entries.append(f"{relative_path}\t{pdf_stat.st_size}\t{pdf_stat.st_mtime_ns}")
    payload = "\n".join(entries)
    return sha256(payload.encode("utf-8", errors="surrogatepass")).hexdigest()


def _corpus_fingerprint_path(
    model_cache_root: Path, config: DocumentCorpusConfig
) -> Path:
    fallback = CacheFallback(
        allowed_root=model_cache_root,
        root=model_cache_root / CACHE_ROOT_NAME,
        relative_path=PurePath(CORPUS_FINGERPRINT_NAME),
    )
    return safe_cache_target(config.source_root, fallback)


# 캐시/지문 파일이 모두 존재하고 지문이 현재 소스 상태와 일치할 때만 캐시된
# 행을 돌려준다. 그 외 모든 경우(파일 없음, 지문 불일치, 파싱 실패)는 캐시
# 미스로 취급해 startup_corpus_rows가 다시 빌드하게 한다.
def _cached_rows_if_fresh(
    cache_path: Path, fingerprint_path: Path, current_fingerprint: str
) -> tuple[CorpusMetadataRow, ...] | None:
    if not cache_path.is_file() or not fingerprint_path.is_file():
        return None
    try:
        stored = parse_json_object_for_field(
            fingerprint_path.read_text(encoding="utf-8"),
            "document_corpus_fingerprint",
        )
    except (OSError, ContractValidationError):
        return None
    if stored.get("fingerprint") != current_fingerprint:
        return None
    try:
        return _read_cached_rows(cache_path)
    except (OSError, ContractValidationError):
        return None


def _write_fingerprint(path: Path, fingerprint: str, boundary: _WriteBoundary) -> None:
    payload = (
        json.dumps({"fingerprint": fingerprint}, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    _guard_write_directory(path.parent, boundary)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(path, payload, boundary)


def _read_cached_rows(cache_path: Path) -> tuple[CorpusMetadataRow, ...]:
    lines = cache_path.read_text(encoding="utf-8").splitlines()
    rows = tuple(_row_from_json(line) for line in lines if line.strip())
    if not rows:
        field = "document_corpus"
        reason = "cached corpus metadata is empty"
        raise ContractValidationError(field, reason)
    return rows


def _row_from_json(line: str) -> CorpusMetadataRow:
    record = parse_json_object_for_field(line, "document_corpus_metadata")
    text_value = record.get("text")
    text = _optional_string_field(text_value, "document_corpus_metadata.text")
    pages_value = record.get("pages")
    if not isinstance(pages_value, list):
        field = "document_corpus_metadata.pages"
        reason = "must be an array"
        raise ContractValidationError(field, reason)
    return CorpusMetadataRow(
        document_id=_string_field(record, "document_id"),
        relative_path=_string_field(record, "relative_path"),
        status=_string_field(record, "status"),
        text=text,
        pages=tuple(_page_from_json(page) for page in pages_value),
    )


def _page_from_json(page: JsonValue) -> CorpusPageText:
    if not isinstance(page, dict):
        field = "document_corpus_metadata.pages"
        reason = "each page must be a JSON object"
        raise ContractValidationError(field, reason)
    page_number = page.get("page_number")
    if not isinstance(page_number, int) or isinstance(page_number, bool):
        field = "document_corpus_metadata.pages.page_number"
        reason = "must be an integer"
        raise ContractValidationError(field, reason)
    return CorpusPageText(page_number=page_number, text=_string_field(page, "text"))


def _string_field(record: JsonObject, field_name: str) -> str:
    value = record.get(field_name)
    if not isinstance(value, str):
        field = f"document_corpus_metadata.{field_name}"
        reason = "must be a string"
        raise ContractValidationError(field, reason)
    return value


def _optional_string_field(value: JsonValue | None, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        reason = "must be a string or null"
        raise ContractValidationError(field, reason)
    return value


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


# 소스 문서 루트를 결정한다: VCA_DOCUMENT_CORPUS_DIR 환경변수를 반드시 요구한다
# (개인 머신 경로로 조용히 폴백하지 않는다). startup_runner의 쓰기 경계 계산과
# sourceRoot 판정 양쪽에서 호출한다.
def startup_document_source_root() -> Path:
    """Return the configured source document root for startup RAG."""
    raw_source_root = os.environ.get(DOCUMENT_CORPUS_DIR_ENV)
    if raw_source_root is None or not raw_source_root.strip():
        field = DOCUMENT_CORPUS_DIR_ENV
        reason = "must be set to the source document corpus directory"
        raise ContractValidationError(field, reason)
    return Path(raw_source_root)


def _document_source_root() -> Path:
    return startup_document_source_root()
