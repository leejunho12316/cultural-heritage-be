"""Typed JSON-object parsing for Qwen bridge artifact boundaries."""

from modules.shared.json_object import (
    JsonObject,
    JsonScalar,
    JsonValue,
    parse_json_object_for_field,
)

__all__ = ("JsonObject", "JsonScalar", "JsonValue", "parse_json_object")


# qwen 브리지 아티팩트 파싱 전체가 거치는 단일 진입점. 필드 이름을
# "qwen_bridge_results"로 고정해 에러 메시지 출처를 일관되게 유지한다.
def parse_json_object(text: str) -> JsonObject:
    """Parse one complete JSON object without exposing untyped decoder values."""
    return parse_json_object_for_field(text, "qwen_bridge_results")
