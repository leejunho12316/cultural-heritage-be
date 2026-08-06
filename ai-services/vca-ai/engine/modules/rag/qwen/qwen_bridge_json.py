"""Typed JSON-object parsing for Qwen bridge artifact boundaries."""

from modules.shared.json_object import (
    JsonObject,
    JsonScalar,
    JsonValue,
    parse_json_object_for_field,
)

__all__ = ("JsonObject", "JsonScalar", "JsonValue", "parse_json_object")


def parse_json_object(text: str) -> JsonObject:
    """Parse one complete JSON object without exposing untyped decoder values."""
    return parse_json_object_for_field(text, "qwen_bridge_results")
