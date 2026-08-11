"""2. 처리 전 상태조사 — 육안조사 결과와 X-ray 조사 결과를 하나의 섹션 안에서
육안조사/X-ray 두 부분(parts)으로 나눠 서술한다 (이 순서 고정 - 육안조사가
먼저, X-ray가 다음).

예전엔 두 조사 결과를 한 번의 LLM 호출로 묶어서 하나의 body 문자열로 만들었다.
문제는 사진 배치였다 - docx_export가 사진을 "섹션" 단위로만 붙일 수 있는데,
육안조사(문양) 사진과 X-ray 사진이 전부 "pre_investigation" 키 하나에 뭉쳐
있으니, 실제 참고 보고서들(예: 규장각 보고서)처럼 "문양 서술 직후 문양 사진,
그다음 다음 조사 항목"으로는 절대 배치할 수 없었다 - 사진이 섹션 전체 서술이
다 끝난 뒤에야 붙었다.

그래서 육안조사/X-ray를 각각 독립적인 LLM 호출로 분리해서 "parts" 리스트로
반환한다. docx_export는 각 part의 body를 쓴 직후 그 part의 key로 사진을
붙이므로, 이제 문양 사진은 육안조사 서술 뒤, X-ray 사진은 X-ray 서술 뒤에
정확히 붙는다. 어느 한쪽 데이터가 없으면(예: 육안조사 결과 없음) 그 조사에
대해 근거 없는 문장을 지어내지 않도록 해당 part 자체를 생략한다.
"""

from typing import Any

from ..state import State
from .common import build_section_via_llm

_VISUAL_TITLE = "1) 육안조사"
_XRAY_TITLE = "2) X-ray 조사"

_VISUAL_SYSTEM_PROMPT = """당신은 문화유산 보존처리 보고서를 작성하는 전문가입니다.
아래 육안조사(재질·문양·손상) 결과를 바탕으로, 정식 보존처리 보고서
"처리 전 상태조사" 섹션 중 육안조사 부분 문장을 작성하세요.

- 육안조사에서 확인된 손상·문양 판단을 반영하세요.
- 근거 없는 내용을 추가하지 마세요."""

_XRAY_SYSTEM_PROMPT = """당신은 문화유산 보존처리 보고서를 작성하는 전문가입니다.
아래 X-ray 조사 결과를 바탕으로, 정식 보존처리 보고서 "처리 전 상태조사"
섹션 중 X-ray 조사 부분 문장을 작성하세요.

- X-ray에서 확인된 확정 이상영역(위치·소견)을 반영하세요.
- 근거 없는 내용을 추가하지 마세요."""


def pre_investigation_node(state: State) -> dict[str, Any]:
    # 순서 고정: 육안조사가 1번, X-ray가 2번 (parts 리스트 순서 = docx 렌더링 순서).
    parts: list[dict[str, Any]] = []

    pottery_inspection = state.get("pottery_inspection") or {}
    inspection_text = pottery_inspection.get("inspection_text")

    # 육안조사 결과가 있을 때만 이 part를 만든다.
    if inspection_text:
        visual_section = build_section_via_llm(
            system_prompt=_VISUAL_SYSTEM_PROMPT,
            data_context=f"""
[육안조사 결과]
{inspection_text}
""",
            rag_query="보존처리 보고서 처리 전 상태조사 육안조사 손상 문양 판단",
        )
        parts.append({
            "key": "pre_investigation_visual",
            "title": _VISUAL_TITLE,
            "body": visual_section.body,
        })

    xray_regions = state.get("xray_regions") or []
    damage_regions = [r for r in xray_regions if r.get("review_decision") == "damage"]
    xray_report_text = state.get("xray_report_text")

    # X-ray 데이터(문안 또는 확정 이상영역)가 하나라도 있을 때만 이 part를 만든다.
    if xray_report_text or damage_regions:
        region_lines = "\n".join(
            f"- {r.get('region_code', '')}: {r.get('position', '')} / {r.get('user_note', '')}"
            for r in damage_regions
        ) or "(확정된 이상영역 없음)"

        xray_section = build_section_via_llm(
            system_prompt=_XRAY_SYSTEM_PROMPT,
            data_context=f"""
[X-ray 상태조사 문안]
{xray_report_text or "(없음)"}

[X-ray 확정 이상영역 ({len(damage_regions)}건)]
{region_lines}
""",
            rag_query="보존처리 보고서 처리 전 상태조사 X-ray 이상영역 판단",
        )
        parts.append({
            "key": "pre_investigation_xray",
            "title": _XRAY_TITLE,
            "body": xray_section.body,
        })

    return {
        "sections": {
            "pre_investigation": {
                "title": "처리 전 상태조사",
                "parts": parts,
            }
        }
    }
