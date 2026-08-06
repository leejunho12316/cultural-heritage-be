"""Resumable per-PDF extraction cache for local document corpus builds."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import NoReturn, TypedDict

from modules.rag.corpus.corpus import CorpusPageText
from modules.shared import ContractValidationError
from modules.shared.json_object import (
    JsonObject,
    JsonValue,
    parse_json_object_for_field,
)


class ExtractionPageJsonRecord(TypedDict):
    """JSONL-compatible page record for one extracted PDF page."""

    page_number: int
    text: str


class ExtractionCacheJsonRecord(TypedDict):
    """JSONL-compatible complete extraction record for one source PDF."""

    relative_path: str
    pages: tuple[ExtractionPageJsonRecord, ...]


@dataclass(frozen=True, slots=True)
class ExtractionCacheEntry:
    """Complete cached extraction payload for one relative source PDF path."""

    relative_path: PurePath
    pages: tuple[CorpusPageText, ...]


def read_extraction_cache(path: Path | None) -> tuple[ExtractionCacheEntry, ...]:
    """Read complete cached PDF extraction records, ignoring a truncated tail."""
    if path is None or not path.is_file():
        return ()
    lines = path.read_text(encoding="utf-8").splitlines()
    entries: list[ExtractionCacheEntry] = []
    for index, line in enumerate(lines):
        entry = _entry_from_line(line, index, len(lines))
        if entry is not None:
            entries.append(entry)
    return tuple(entries)


def append_extraction_cache(
    path: Path | None,
    relative_path: PurePath,
    pages: tuple[CorpusPageText, ...],
) -> None:
    """Append one complete source-PDF extraction record when caching is enabled."""
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        _json_record(relative_path, pages),
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    with path.open("a", encoding="utf-8") as cache_file:
        _ = cache_file.write(payload)


def _entry_from_line(
    line: str,
    index: int,
    line_count: int,
) -> ExtractionCacheEntry | None:
    if not line.strip():
        return None
    try:
        record = _parse_json_object(line)
    except ContractValidationError:
        if index == line_count - 1:
            return None
        raise
    return ExtractionCacheEntry(_relative_pdf_path(record), _page_rows(record))


def _parse_json_object(line: str) -> JsonObject:
    return parse_json_object_for_field(line, "extraction_cache")


def _json_record(
    relative_path: PurePath,
    pages: tuple[CorpusPageText, ...],
) -> ExtractionCacheJsonRecord:
    return {
        "pages": tuple(
            {"page_number": page.page_number, "text": page.text} for page in pages
        ),
        "relative_path": str(relative_path),
    }


def _relative_pdf_path(record: JsonObject) -> PurePath:
    relative_path = PurePath(_string(record, "relative_path"))
    if (
        relative_path == PurePath()
        or relative_path.is_absolute()
        or ".." in relative_path.parts
        or relative_path.suffix.casefold() != ".pdf"
    ):
        _raise_contract("relative_path", "must be a contained relative PDF path")
    return relative_path


def _page_rows(record: JsonObject) -> tuple[CorpusPageText, ...]:
    value = record.get("pages")
    if not isinstance(value, list):
        _raise_contract("pages", "must be an array")
    return tuple(_page_row(_object(page, "pages")) for page in value)


def _page_row(record: JsonObject) -> CorpusPageText:
    page_number = record.get("page_number")
    if not isinstance(page_number, int) or isinstance(page_number, bool):
        _raise_contract("page_number", "must be an integer")
    return CorpusPageText(page_number=page_number, text=_string(record, "text"))


def _object(value: JsonValue, field: str) -> JsonObject:
    if not isinstance(value, dict):
        _raise_contract(field, "must contain JSON objects")
    return value


def _string(record: JsonObject, field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        _raise_contract(field, "must be a non-blank string")
    return value


def _raise_contract(field: str, reason: str) -> NoReturn:
    raise ContractValidationError(field, reason)
