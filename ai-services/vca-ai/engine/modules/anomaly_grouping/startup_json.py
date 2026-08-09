"""JSON boundary helpers for anomaly grouping startup artifacts."""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

from modules.report_generating.json_parser import parse_json_object
from modules.shared import ContractValidationError

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from modules.report_generating.models import JsonObject, JsonValue


def raise_contract(field: str, reason: str) -> NoReturn:
    """Raise a typed contract error for malformed startup artifacts."""
    raise ContractValidationError(field, reason)


# startup_runner.py에서 mask_refining/rag 단계가 남긴 JSONL 사이드카(예:
# refined_records.jsonl, rag_candidate_evidence.jsonl)를 읽을 때 공통으로 쓰인다.
def read_jsonl_objects(path: Path) -> tuple[JsonObject, ...]:
    """Read a JSONL file whose nonblank lines must be JSON objects."""
    return tuple(
        parse_json_object(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


# mask_refining이 남긴 records.json(최상위가 배열)을 JSONL과 다른 포맷으로
# 읽어야 할 때 쓰는 헬퍼. 배열을 감싸 재사용 중인 객체 파서로 넘긴다.
def records(path: Path) -> tuple[JsonObject, ...]:
    """Read a mask-refining records.json array as JSON objects."""
    payload = parse_json_object('{"records":' + path.read_text(encoding="utf-8") + "}")
    value = payload.get("records")
    if not isinstance(value, list):
        raise_contract("records_json", "must be a list")
    return tuple(json_object(item, "records_json") for item in value)


def json_object(value: JsonValue | None, field: str) -> JsonObject:
    """Parse one recursive JSON value as an object."""
    if not isinstance(value, dict):
        raise_contract(field, "must be a JSON object")
    return value


def string(record: JsonObject, field: str) -> str:
    """Parse a required nonblank string field."""
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise_contract(field, "must be a nonblank string")
    return value


def optional_string(record: JsonObject, field: str) -> str | None:
    """Parse an optional nonblank string field."""
    value = record.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise_contract(field, "must be a nonblank string")
    return value


def strings(record: JsonObject, field: str) -> tuple[str, ...]:
    """Parse a required string-list field."""
    value = record.get(field)
    if not isinstance(value, list):
        raise_contract(field, "must be a string list")
    values: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise_contract(field, "must be a string list")
        values.append(item)
    return tuple(values)


def numbers(record: JsonObject, field: str) -> tuple[float, ...]:
    """Parse a required numeric-list field."""
    value = record.get(field)
    if not isinstance(value, list):
        raise_contract(field, "must be a number list")
    values: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int | float):
            raise_contract(field, "must be a number list")
        values.append(float(item))
    return tuple(values)


def bool_value(record: JsonObject, field: str) -> bool:
    """Parse a required boolean field."""
    value = record.get(field)
    if not isinstance(value, bool):
        raise_contract(field, "must be a boolean")
    return value


def float_value(record: JsonObject, field: str) -> float:
    """Parse a required numeric field as float."""
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise_contract(field, "must be a number")
    return float(value)


# startup_runner.py의 _seed_lane_priority 등에서 사용. 최초 등장 순서를
# 유지하면서 중복/빈 문자열을 제거한다(순서가 우선순위로 쓰이는 곳이 있으므로
# 정렬하지 않는다).
def unique_strings(values: Iterable[str]) -> tuple[str, ...]:
    """Return nonblank strings in first-seen order without duplicates."""
    return tuple(dict.fromkeys(value for value in values if value.strip()))
