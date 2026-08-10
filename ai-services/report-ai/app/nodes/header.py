"""1. 유물 기본정보 — LLM 호출 없이 relic_info를 그대로 표 형태로 옮긴다."""

from typing import Any

from ..state import State


def header_node(state: State) -> dict[str, Any]:
    relic_info = state.get("relic_info") or {}
    return {
        "sections": {
            "header": {
                "title": "유물 기본정보",
                "fields": {
                    "관리번호": relic_info.get("artifact_code") or relic_info.get("id"),
                    "명칭": relic_info.get("name"),
                    "재질": relic_info.get("material"),
                    "시대": relic_info.get("period"),
                    "무게": relic_info.get("weight"),
                    "접합부위": relic_info.get("bondingArea"),
                    "처리목적": relic_info.get("treatmentPurpose"),
                },
            }
        }
    }
