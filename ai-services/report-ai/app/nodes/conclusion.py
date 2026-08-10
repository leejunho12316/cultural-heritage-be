"""8. 처리 결과 및 종합 결론 — 앞선 모든 섹션을 종합해 결론을 작성한다."""

from typing import Any

from ..state import State
from .common import build_section_via_llm

SYSTEM_PROMPT = """당신은 문화유산 보존처리 보고서를 작성하는 전문가입니다.
아래는 이 유물의 보존처리 보고서에 이미 작성된 각 섹션 내용입니다.
이를 종합해서 "처리 결과 및 종합 결론" 섹션을 작성하세요.

- 새로운 사실을 추가하지 말고, 앞선 섹션들의 내용을 요약·종합하세요.
- 처리 전후 상태 변화와 향후 관리 시 주의할 점을 간단히 언급하세요."""

_ORDER = [
    "pre_investigation",
    "disassembly",
    "cleaning",
    "reinforcement",
    "bonding",
    "restoration",
]


def conclusion_node(state: State) -> dict[str, Any]:
    sections = state.get("sections") or {}
    data_context = (
        "\n\n".join(
            f"[{sections[key]['title']}]\n{sections[key]['body']}"
            for key in _ORDER
            if key in sections
        )
        or "(작성된 섹션 없음)"
    )

    section = build_section_via_llm(
        system_prompt=SYSTEM_PROMPT,
        data_context=data_context,
        rag_query="보존처리 보고서 결론 처리 결과 향후 관리",
    )
    return {"sections": {"conclusion": section.model_dump()}}
