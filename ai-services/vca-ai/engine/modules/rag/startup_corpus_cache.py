"""Startup-safe cache for source-backed document corpus metadata."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Final

from modules.rag.corpus.cache import CacheFallback, safe_cache_target
from modules.rag.corpus.corpus import CorpusMetadataRow, CorpusPageText
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
from modules.shared.json_object import (
    JsonObject,
    JsonValue,
    parse_json_object_for_field,
)

CACHE_ROOT_NAME: Final = "rag"
CORPUS_CACHE_NAME: Final = "document_corpus_metadata.jsonl"
DOCUMENT_CORPUS_DIR_ENV: Final = "VCA_DOCUMENT_CORPUS_DIR"


@dataclass(frozen=True, slots=True)
class _WriteBoundary:
    root: Path
    source_root: Path


# startup_runner._locked_corpus_vector_index가 락을 잡은 상태에서 호출하는
# 코퍼스 메타데이터 진입점. 캐시 재사용 조건은 아래 docstring 참고.
def startup_corpus_rows(model_cache_root: Path) -> tuple[CorpusMetadataRow, ...]:
    """캐시된 코퍼스 빌드가 이미 있으면 그걸 재사용한다.

    캐시 신선도는 소스 PDF와 자동으로 비교해 판단하지 않는다(fingerprint/mtime
    비교 없음) - document_corpus_metadata.jsonl이 존재하고 파싱만 되면
    그대로 신뢰한다. 코퍼스를 갱신하는 건 운영자의 명시적 조작이다: 캐시
    파일(또는 `rag/` 캐시 디렉터리 전체)을 지우고 다시 실행하면 전체
    재빌드로 넘어간다.
    """
    _guard_model_cache_root(model_cache_root)
    config = _document_corpus_config(model_cache_root)
    cache_path = _corpus_cache_path(model_cache_root, config)
    boundary = _WriteBoundary(model_cache_root, config.source_root)
    cached_rows = _cached_rows_if_present(cache_path)
    if cached_rows is not None:
        return cached_rows
    rows = build_document_corpus(config, PdfTextExtractor())
    _write_corpus_cache(cache_path, rows, boundary)
    return rows


# 캐시 파일이 존재하고 파싱 가능할 때만 캐시된 행을 돌려준다. 그 외 모든
# 경우(파일 없음, 파싱 실패, 빈 내용)는 캐시 미스로 취급해
# startup_corpus_rows가 소스 PDF에서 다시 빌드하게 한다 - 소스 PDF가
# 그새 바뀌었는지는 더 이상 확인하지 않는다(명시적 삭제로만 갱신).
def _cached_rows_if_present(
    cache_path: Path,
) -> tuple[CorpusMetadataRow, ...] | None:
    if not cache_path.is_file():
        return None
    try:
        return _read_cached_rows(cache_path)
    except (OSError, ContractValidationError):
        return None


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
