"""Typed JSON-object parser for artifact boundary readers."""

from __future__ import annotations

from string import hexdigits
from typing import Final, NoReturn

from modules.shared.errors import ContractValidationError

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]

_CONTROL_CHARACTER_LIMIT: Final = 0x20
_UNICODE_ESCAPE_LENGTH: Final = 4
_HIGH_SURROGATE_START: Final = 0xD800
_HIGH_SURROGATE_END: Final = 0xDBFF
_LOW_SURROGATE_START: Final = 0xDC00
_LOW_SURROGATE_END: Final = 0xDFFF
_SURROGATE_PAIR_OFFSET: Final = 0x10000
_JSON_LITERALS: Final = (("true", True), ("false", False), ("null", None))
_SIMPLE_ESCAPES: Final = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


def parse_json_object_for_field(text: str, field: str) -> JsonObject:
    """Parse one complete JSON object and report contract errors under field."""
    value, position = _value(text, _whitespace(text, 0), field)
    if _whitespace(text, position) != len(text):
        _invalid(field, "must contain exactly one JSON value")
    if not isinstance(value, dict):
        _invalid(field, "must be a JSON object")
    return value


def _value(text: str, position: int, field: str) -> tuple[JsonValue, int]:
    if position == len(text):
        _invalid(field, "is incomplete")
    character = text[position]
    if character == "{":
        return _object(text, position, field)
    if character == "[":
        return _array(text, position, field)
    if character == '"':
        return _string(text, position, field)
    if character in ("t", "f", "n"):
        return _literal(text, position, field)
    if character == "-" or character.isdigit():
        return _number(text, position, field)
    return _invalid(field, "contains an invalid JSON value")


def _object(text: str, position: int, field: str) -> tuple[JsonObject, int]:
    fields: JsonObject = {}
    position = _whitespace(text, position + 1)
    if _at(text, position, field) == "}":
        return fields, position + 1
    while True:
        if _at(text, position, field) != '"':
            _invalid(field, "object field name must be a string")
        name, position = _string(text, position, field)
        position = _whitespace(text, position)
        if _at(text, position, field) != ":":
            _invalid(field, "object field must include a colon")
        value, position = _value(text, _whitespace(text, position + 1), field)
        if name in fields:
            _invalid(field, "object fields must be unique")
        fields[name] = value
        position = _whitespace(text, position)
        delimiter = _at(text, position, field)
        if delimiter == "}":
            return fields, position + 1
        if delimiter != ",":
            _invalid(field, "object fields must be comma-separated")
        position = _whitespace(text, position + 1)


def _array(text: str, position: int, field: str) -> tuple[list[JsonValue], int]:
    values: list[JsonValue] = []
    position = _whitespace(text, position + 1)
    if _at(text, position, field) == "]":
        return values, position + 1
    while True:
        value, position = _value(text, position, field)
        values.append(value)
        position = _whitespace(text, position)
        delimiter = _at(text, position, field)
        if delimiter == "]":
            return values, position + 1
        if delimiter != ",":
            _invalid(field, "array values must be comma-separated")
        position = _whitespace(text, position + 1)


def _string(text: str, position: int, field: str) -> tuple[str, int]:
    characters: list[str] = []
    position += 1
    while True:
        character = _at(text, position, field)
        if character == '"':
            return "".join(characters), position + 1
        if ord(character) < _CONTROL_CHARACTER_LIMIT:
            _invalid(field, "strings cannot contain control characters")
        if character != "\\":
            characters.append(character)
            position += 1
            continue
        escaped, position = _escape(text, position + 1, field)
        characters.append(escaped)


def _escape(text: str, position: int, field: str) -> tuple[str, int]:
    character = _at(text, position, field)
    simple = _SIMPLE_ESCAPES.get(character)
    if simple is not None:
        return simple, position + 1
    if character != "u":
        return _invalid(field, "contains an invalid escape")
    code_point, position = _unicode_escape(text, position + 1, field)
    if _HIGH_SURROGATE_START <= code_point <= _HIGH_SURROGATE_END:
        code_point, position = _low_surrogate_pair(text, position, code_point)
    return chr(code_point), position


def _unicode_escape(text: str, position: int, field: str) -> tuple[int, int]:
    hexadecimal = text[position : position + _UNICODE_ESCAPE_LENGTH]
    if len(hexadecimal) != _UNICODE_ESCAPE_LENGTH or any(
        digit not in hexdigits for digit in hexadecimal
    ):
        _invalid(field, "contains an invalid unicode escape")
    return int(hexadecimal, 16), position + _UNICODE_ESCAPE_LENGTH


def _low_surrogate_pair(text: str, position: int, high: int) -> tuple[int, int]:
    # JSON 문자열은 astral-plane 문자(U+FFFF보다 큰 코드포인트)를 UTF-16
    # 서로게이트 페어로 인코딩한다: \uXXXX 이스케이프 2개를 다시 하나의
    # 코드포인트로 합쳐야 한다. 각 이스케이프를 따로 chr()로 만드는 대신
    # 여기서 미리 합쳐두는 게, 짝이 없는(unpaired) 서로게이트가 이후의
    # 해싱/토큰화 소비자에게 그대로 넘어가는 걸 막는 방법이다.
    if text[position : position + 2] != "\\u" or len(text) < position + 6:
        return high, position
    low_hexadecimal = text[position + 2 : position + 6]
    if any(digit not in hexdigits for digit in low_hexadecimal):
        return high, position
    low = int(low_hexadecimal, 16)
    if not (_LOW_SURROGATE_START <= low <= _LOW_SURROGATE_END):
        return high, position
    combined = (
        _SURROGATE_PAIR_OFFSET
        + ((high - _HIGH_SURROGATE_START) << 10)
        + (low - _LOW_SURROGATE_START)
    )
    return combined, position + 6


def _literal(text: str, position: int, field: str) -> tuple[JsonScalar, int]:
    for literal, value in _JSON_LITERALS:
        if text.startswith(literal, position):
            return value, position + len(literal)
    return _invalid(field, "contains an invalid JSON literal")


def _number(text: str, position: int, field: str) -> tuple[int | float, int]:
    start = position
    if _at(text, position, field) == "-":
        position += 1
    first_digit = _at(text, position, field)
    if first_digit == "0":
        position += 1
    elif first_digit.isdigit():
        while _at(text, position, field).isdigit():
            position += 1
    else:
        _invalid(field, "contains an invalid JSON number")
    if _at(text, position, field) == ".":
        position = _fraction(text, position + 1, field)
    if _at(text, position, field) in ("e", "E"):
        position = _exponent(text, position + 1, field)
    raw = text[start:position]
    if "." in raw or "e" in raw.casefold():
        return float(raw), position
    return int(raw), position


def _fraction(text: str, position: int, field: str) -> int:
    if not _at(text, position, field).isdigit():
        _invalid(field, "fraction requires a digit")
    while _at(text, position, field).isdigit():
        position += 1
    return position


def _exponent(text: str, position: int, field: str) -> int:
    if _at(text, position, field) in ("+", "-"):
        position += 1
    if not _at(text, position, field).isdigit():
        _invalid(field, "exponent requires a digit")
    while _at(text, position, field).isdigit():
        position += 1
    return position


def _whitespace(text: str, position: int) -> int:
    while position < len(text) and text[position] in (" ", "\t", "\n", "\r"):
        position += 1
    return position


def _at(text: str, position: int, field: str) -> str:
    if position >= len(text):
        _invalid(field, "is incomplete")
    return text[position]


def _invalid(field: str, reason: str) -> NoReturn:
    raise ContractValidationError(field, f"invalid JSON: {reason}")
