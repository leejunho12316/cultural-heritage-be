from __future__ import annotations

import json

import pytest

from modules.shared.errors import ContractValidationError
from modules.shared.json_object import parse_json_object_for_field


def test_parse_json_object_for_field_reads_plain_fields() -> None:
    # Given: a simple JSON object with scalar, nested, and list values.
    text = '{"a": "text", "b": 1, "c": [true, false, null], "d": {"e": 1.5}}'

    # When: the object is parsed at its trust boundary.
    parsed = parse_json_object_for_field(text, "example")

    # Then: every JSON value type round-trips to its Python equivalent.
    assert parsed == {"a": "text", "b": 1, "c": [True, False, None], "d": {"e": 1.5}}


def test_parse_json_object_for_field_recombines_surrogate_pair_escapes() -> None:
    # Given: a JSON string produced by json.dumps(ensure_ascii=True) that
    # contains a real astral-plane character (here: an emoji outside the
    # Basic Multilingual Plane). Spec-compliant JSON encodes such characters
    # as a UTF-16 surrogate pair of two \\uXXXX escapes.
    original = "glyph \U0001f600 done"
    text = json.dumps({"field": original})

    # When: this project's own boundary parser reads the escaped text back.
    parsed = parse_json_object_for_field(text, "example")

    # Then: the surrogate pair is recombined into the original character
    # instead of being left as two lone (unpaired) surrogates - unpaired
    # surrogates crash UTF-8 encoding and the HuggingFace tokenizer later.
    field = parsed["field"]
    assert field == original
    assert isinstance(field, str)
    assert field.encode("utf-8") == original.encode("utf-8")


def test_parse_json_object_for_field_keeps_unpaired_high_surrogate() -> None:
    # Given: a high surrogate escape not followed by a matching low surrogate.
    text = '{"field": "broken \\ud83d glyph"}'

    # When: the boundary parser reads the malformed-but-syntactically-valid text.
    parsed = parse_json_object_for_field(text, "example")

    # Then: the lone surrogate is preserved rather than silently dropped or
    # misparsed, matching this parser's existing permissive behavior.
    field = parsed["field"]
    assert field == "broken \ud83d glyph"


def test_parse_json_object_for_field_rejects_incomplete_object() -> None:
    # Given: JSON text with a missing closing brace.
    text = '{"field": "value"'

    # When/Then: the boundary parser fails closed instead of guessing.
    with pytest.raises(ContractValidationError):
        _ = parse_json_object_for_field(text, "example")
