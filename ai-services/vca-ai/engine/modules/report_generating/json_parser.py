"""Typed JSON parser that does not leak ``Any`` across report boundaries."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, NoReturn

from modules.shared import ContractValidationError

if TYPE_CHECKING:
    from modules.report_generating.models import JsonObject, JsonValue

_NUMBER_PATTERN = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


# io.py의 read_json_object가 호출하는 공개 진입점. 표준 json.loads 대신 직접
# 파서를 둔 이유는 모듈 docstring대로 Any 타입이 리포트 경계를 넘어 새는 것을
# 막기 위함이다.
def parse_json_object(text: str) -> JsonObject:
    """Parse one complete JSON object into recursive JSON value types."""
    value, position = _parse_value(text, 0)
    if _skip_whitespace(text, position) != len(text) or not isinstance(value, dict):
        _invalid("document", "must be one complete JSON object")
    return value


# 재귀 하강 파서의 중앙 디스패치. 다음 토큰 종류를 보고 객체/배열/문자열/
# 리터럴/숫자 파서로 분기한다.
def _parse_value(text: str, position: int) -> tuple[JsonValue, int]:
    start = _skip_whitespace(text, position)
    marker = _marker(text, start)
    if marker == "{":
        return _parse_object(text, start + 1)
    if marker == "[":
        return _parse_array(text, start + 1)
    if marker == '"':
        return _parse_string(text, start)
    for literal, value in (("true", True), ("false", False), ("null", None)):
        if text.startswith(literal, start):
            return value, start + len(literal)
    match = _NUMBER_PATTERN.match(text, start)
    if match is None:
        _invalid("document", "contains invalid JSON value")
    token = match.group(0)
    value = float(token) if any(sign in token for sign in ".eE") else int(token)
    return value, match.end()


def _parse_object(text: str, position: int) -> tuple[JsonObject, int]:
    result: JsonObject = {}
    cursor = _skip_whitespace(text, position)
    if _marker(text, cursor) == "}":
        return result, cursor + 1
    while True:
        key, cursor = _parse_string(text, cursor)
        value, cursor = _parse_value(text, _expect_marker(text, cursor, ":"))
        result[key] = value
        cursor = _skip_whitespace(text, cursor)
        if _marker(text, cursor) == "}":
            return result, cursor + 1
        cursor = _expect_marker(text, cursor, ",")


def _parse_array(text: str, position: int) -> tuple[list[JsonValue], int]:
    result: list[JsonValue] = []
    cursor = _skip_whitespace(text, position)
    if _marker(text, cursor) == "]":
        return result, cursor + 1
    while True:
        value, cursor = _parse_value(text, cursor)
        result.append(value)
        cursor = _skip_whitespace(text, cursor)
        if _marker(text, cursor) == "]":
            return result, cursor + 1
        cursor = _expect_marker(text, cursor, ",")


def _parse_string(text: str, position: int) -> tuple[str, int]:
    start = _skip_whitespace(text, position)
    if _marker(text, start) != '"':
        _invalid("document", "expected JSON string")
    cursor = start + 1
    characters: list[str] = []
    while cursor < len(text):
        character = text[cursor]
        if character == '"':
            return "".join(characters), cursor + 1
        if character == "\\":
            escaped, cursor = _parse_escape(text, cursor)
            characters.append(escaped)
        else:
            characters.append(character)
        cursor += 1
    return _invalid("document", "contains unterminated JSON string")


def _parse_escape(text: str, position: int) -> tuple[str, int]:
    cursor = position + 1
    character = _marker(text, cursor)
    escaped = {
        '"': '"',
        "\\": "\\",
        "/": "/",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }.get(character)
    if escaped is not None:
        return escaped, cursor
    if character == "u" and cursor + 4 < len(text):
        codepoint = text[cursor + 1 : cursor + 5]
        if all(value in "0123456789abcdefABCDEF" for value in codepoint):
            return chr(int(codepoint, 16)), cursor + 4
    return _invalid("document", "contains invalid JSON string escape")


def _skip_whitespace(text: str, position: int) -> int:
    cursor = position
    while cursor < len(text) and text[cursor] in " \t\n\r":
        cursor += 1
    return cursor


def _expect_marker(text: str, position: int, expected: str) -> int:
    cursor = _skip_whitespace(text, position)
    if _marker(text, cursor) != expected:
        _invalid("document", f"expected {expected}")
    return cursor + 1


def _marker(text: str, position: int) -> str:
    return text[position] if position < len(text) else ""


def _invalid(field: str, reason: str) -> NoReturn:
    raise ContractValidationError(field, reason)
