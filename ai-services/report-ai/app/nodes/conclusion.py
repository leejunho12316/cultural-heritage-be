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


def _render_section(section: dict[str, Any]) -> str:
    """섹션 하나를 LLM 컨텍스트용 텍스트로 변환한다.

    일반 섹션은 {title, body} 형태지만, pre_investigation만 육안조사/X-ray
    두 세부 파트로 나뉘어 {title, parts: [{title, body}, ...]} 형태다
    (pre_investigation_node 참고 - 한쪽 데이터가 없으면 그 part 자체가
    빠질 수 있다). 여기서 그 차이를 흡수해서 conclusion_node는 몰라도
    되게 한다.
    """
    parts = section.get("parts")
    if parts is not None:
        return "\n\n".join(
            f"[{part['title']}]\n{part['body']}"
            for part in parts
            if part.get("body")
        )
    return f"[{section['title']}]\n{section['body']}"


def conclusion_node(state: State) -> dict[str, Any]:
    sections = state.get("sections") or {}
    rendered = [
        text
        for key in _ORDER
        if key in sections
        for text in [_render_section(sections[key])]
        if text
    ]
    data_context = "\n\n".join(rendered) or "(작성된 섹션 없음)"

    section = build_section_via_llm(
        system_prompt=SYSTEM_PROMPT,
        data_context=data_context,
        rag_query="보존처리 보고서 결론 처리 결과 향후 관리",
    )
    return {"sections": {"conclusion": section.model_dump()}}
