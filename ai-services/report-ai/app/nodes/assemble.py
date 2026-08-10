"""9. 전체 조립 — 고정된 도자기 처리보고서 양식 순서대로 report_json을 만든다.

ASSESSMENT_REPORT.report_json에 그대로 들어갈 수 있는 형태로 조립한다.
"""

from typing import Any

from ..state import State

SECTION_ORDER = [
    "header",
    "pre_investigation",
    "disassembly",
    "cleaning",
    "reinforcement",
    "bonding",
    "restoration",
    "conclusion",
]


def assemble_node(state: State) -> dict[str, Any]:
    sections = state.get("sections") or {}
    # "key"를 함께 심어둔다 - title은 LLM이 매번 조금씩 다르게 쓸 수 있어
    # (예: "세척" vs "세척(오염물 제거)") 사진을 단계에 매칭할 때 title
    # 문자열이 아니라 이 안정적인 key를 기준으로 삼는다 (docx_export 참고).
    ordered = [
        {**sections[key], "key": key} for key in SECTION_ORDER if key in sections
    ]
    return {
        "report_json": {
            "report_type": "ceramic_treatment_report",
            "artifact_id": state.get("artifact_id"),
            "sections": ordered,
        }
    }
