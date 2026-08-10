"""2. 처리 전 상태조사 — X-ray 조사 결과와 육안조사 결과를 하나의 섹션으로 종합한다."""

from typing import Any

from ..state import State
from .common import build_section_via_llm

SYSTEM_PROMPT = """당신은 문화유산 보존처리 보고서를 작성하는 전문가입니다.
아래 X-ray 조사 결과와 육안조사(재질·문양·손상) 결과를 종합해서,
정식 보존처리 보고서의 "처리 전 상태조사" 섹션 문장을 작성하세요.

- X-ray에서 확인된 확정 이상영역(위치·소견)과 육안조사에서 확인된 손상·문양 판단을
  모두 반영하세요.
- 두 조사 결과가 없는 항목은 언급하지 마세요. 근거 없는 내용을 추가하지 마세요."""


def pre_investigation_node(state: State) -> dict[str, Any]:
    xray_regions = state.get("xray_regions") or []
    damage_regions = [r for r in xray_regions if r.get("review_decision") == "damage"]
    region_lines = "\n".join(
        f"- {r.get('region_code', '')}: {r.get('position', '')} / {r.get('user_note', '')}"
        for r in damage_regions
    ) or "(확정된 이상영역 없음)"

    pottery_inspection = state.get("pottery_inspection") or {}

    data_context = f"""
[X-ray 상태조사 문안]
{state.get("xray_report_text") or "(없음)"}

[X-ray 확정 이상영역 ({len(damage_regions)}건)]
{region_lines}

[육안조사 결과]
{pottery_inspection.get("inspection_text") or "(없음)"}
"""

    section = build_section_via_llm(
        system_prompt=SYSTEM_PROMPT,
        data_context=data_context,
        rag_query="보존처리 보고서 처리 전 상태조사 X-ray 이상영역 육안조사 손상 문양 판단",
    )
    return {"sections": {"pre_investigation": section.model_dump()}}
