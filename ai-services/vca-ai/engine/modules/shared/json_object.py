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
    hexadecimal = text[position + 1 : position + 1 + _UNICODE_ESCAPE_LENGTH]
    if len(hexadecimal) != _UNICODE_ESCAPE_LENGTH or any(
        digit not in hexdigits for digit in hexadecimal
    ):
        _invalid(field, "contains an invalid unicode escape")
    return chr(int(hexadecimal, 16)), position + 1 + _UNICODE_ESCAPE_LENGTH


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
