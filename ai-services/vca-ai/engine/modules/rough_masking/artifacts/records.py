"""Typed JSON record decoding for detector runner outputs."""

import re
from typing import Final

type JsonAtom = str | int | float | bool | None
type JsonValue = JsonAtom | list[JsonAtom]
type JsonRecord = dict[str, JsonValue]

_NUMBER: Final = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")
_WHITESPACE: Final = " \t\r\n"
_FIRST_CONTROL_CODEPOINT: Final = 0x20
_LITERALS: Final[dict[str, tuple[str, JsonAtom]]] = {
    "t": ("true", True),
    "f": ("false", False),
    "n": ("null", None),
    "N": ("NaN", float("nan")),
    "I": ("Infinity", float("inf")),
}


class _ParseFailure:
    pass


_PARSE_FAILURE: Final = _ParseFailure()
type ParseValue = JsonValue | _ParseFailure


class _RecordJsonParser:
    def __init__(self, text: str) -> None:
        self.text: str = text
        self.index: int = 0

    # decode_records의 진입점. 신뢰할 수 없는 러너 출력 텍스트를 파싱하며,
    # 표준 json 모듈 대신 직접 구현되어 있어 형식이 어긋나면 예외 대신 None을 반환한다.
    def parse_records(self) -> tuple[JsonRecord, ...] | None:
        self._skip_whitespace()
        if not self._consume("["):
            return None
        records: list[JsonRecord] = []
        self._skip_whitespace()
        if self._consume("]"):
            return () if self._finished() else None
        while True:
            record = self._parse_record()
            if record is None:
                return None
            records.append(record)
            self._skip_whitespace()
            if self._consume("]"):
                return tuple(records) if self._finished() else None
            if not self._consume(","):
                return None

    def _parse_record(self) -> JsonRecord | None:
        self._skip_whitespace()
        if not self._consume("{"):
            return None
        record: JsonRecord = {}
        self._skip_whitespace()
        if self._consume("}"):
            return record
        while True:
            member = self._parse_member()
            if member is None:
                return None
            key, value = member
            record[key] = value
            self._skip_whitespace()
            if self._consume("}"):
                return record
            if not self._consume(","):
                return None

    def _parse_member(self) -> tuple[str, JsonValue] | None:
        key = self._parse_string()
        if key is None:
            return None
        self._skip_whitespace()
        if not self._consume(":"):
            return None
        value = self._parse_value()
        if isinstance(value, _ParseFailure):
            return None
        return key, value

    def _parse_value(self) -> ParseValue:
        self._skip_whitespace()
        if self._at_end():
            return _PARSE_FAILURE
        match self.text[self.index]:
            case '"':
                value = self._parse_string()
                return _PARSE_FAILURE if value is None else value
            case "[":
                return self._parse_atom_list()
            case "-" if self.text.startswith("-Infinity", self.index):
                return self._consume_literal("-Infinity", float("-inf"))
            case _:
                literal = _LITERALS.get(self.text[self.index])
                return (
                    self._parse_number()
                    if literal is None
                    else self._consume_literal(*literal)
                )

    def _parse_atom_list(self) -> list[JsonAtom] | _ParseFailure:
        if not self._consume("["):
            return _PARSE_FAILURE
        values: list[JsonAtom] = []
        self._skip_whitespace()
        if self._consume("]"):
            return values
        while True:
            value = self._parse_value()
            if isinstance(value, _ParseFailure | list):
                return _PARSE_FAILURE
            values.append(value)
            self._skip_whitespace()
            if self._consume("]"):
                return values
            if not self._consume(","):
                return _PARSE_FAILURE

    def _parse_number(self) -> int | float | _ParseFailure:
        matched = _NUMBER.match(self.text, self.index)
        if matched is None:
            return _PARSE_FAILURE
        token = matched.group()
        self.index = matched.end()
        return float(token) if any(marker in token for marker in ".eE") else int(token)

    def _parse_string(self) -> str | None:
        self._skip_whitespace()
        if not self._consume('"'):
            return None
        parts: list[str] = []
        while not self._at_end():
            character = self.text[self.index]
            self.index += 1
            if character == '"':
                return "".join(parts)
            if character == "\\":
                escaped = self._parse_escape()
                if escaped is None:
                    return None
                parts.append(escaped)
            elif ord(character) >= _FIRST_CONTROL_CODEPOINT:
                parts.append(character)
            else:
                return None
        return None

    def _parse_escape(self) -> str | None:
        if self._at_end():
            return None
        escaped = self.text[self.index]
        self.index += 1
        replacements = {
            '"': '"',
            "\\": "\\",
            "/": "/",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
        }
        if escaped in replacements:
            return replacements[escaped]
        if escaped != "u" or self.index + 4 > len(self.text):
            return None
        codepoint = self.text[self.index : self.index + 4]
        if not all(character in "0123456789abcdefABCDEF" for character in codepoint):
            return None
        self.index += 4
        return chr(int(codepoint, 16))

    def _consume_literal(
        self, literal: str, value: JsonAtom
    ) -> JsonAtom | _ParseFailure:
        if not self.text.startswith(literal, self.index):
            return _PARSE_FAILURE
        self.index += len(literal)
        return value

    def _skip_whitespace(self) -> None:
        while not self._at_end() and self.text[self.index] in _WHITESPACE:
            self.index += 1

    def _consume(self, token: str) -> bool:
        if self.text.startswith(token, self.index):
            self.index += len(token)
            return True
        return False

    def _at_end(self) -> bool:
        return self.index >= len(self.text)

    def _finished(self) -> bool:
        self._skip_whitespace()
        return self._at_end()


def decode_records(text: str) -> tuple[JsonRecord, ...] | None:
    """Decode runner JSON at the untrusted boundary before normalization."""
    return _RecordJsonParser(text).parse_records()
