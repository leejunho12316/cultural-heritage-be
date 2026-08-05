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
    ordered = [sections[key] for key in SECTION_ORDER if key in sections]
    return {
        "report_json": {
            "report_type": "ceramic_treatment_report",
            "artifact_id": state.get("artifact_id"),
            "sections": ordered,
        }
    }
