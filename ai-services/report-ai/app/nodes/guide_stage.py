"""3~7. 해체·세척·강화처리·접합·복원 — 보존가이드 결과를 정식 보고서 문장으로 재작성.

다섯 단계가 구조적으로 동일한 패턴(원본 데이터 → 보고서 문장)이라
단계별로 파일을 따로 두지 않고 팩토리 함수로 노드를 생성한다.
"""

import json
from typing import Any

from ..state import State
from .common import build_section_via_llm

_STAGE_TITLES = {
    "disassembly": "해체",
    "cleaning": "세척",
    "reinforcement": "강화처리",
    "bonding": "접합",
    "restoration": "복원",
}

_SYSTEM_PROMPT_TEMPLATE = """당신은 문화유산 보존처리 보고서를 작성하는 전문가입니다.
아래 JSON은 "{title}" 단계에서 실제로 기록된 작업 데이터입니다
(AI 추천값, 사용자가 최종 선택한 확정값, 담당자 메모 등이 섞여 있습니다).
이 데이터를 근거로 정식 보존처리 보고서의 "{title}" 섹션 문장을 작성하세요.

- AI 추천값과 확정값이 다르면, 실제로 적용된 확정값을 기준으로 서술하세요.
- 담당자 메모(memo)가 구어체라도 정식 보고서체로 다듬어 반영하세요.
- 데이터에 없는 재료·작업을 지어내지 마세요."""


def make_guide_stage_node(stage_key: str):
    title = _STAGE_TITLES[stage_key]
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(title=title)

    def node(state: State) -> dict[str, Any]:
        guide_result = state.get("guide_result") or {}
        if stage_key not in guide_result:
            # 이 유물의 flow에 포함되지 않은 단계 — 섹션 자체를 만들지 않는다.
            return {}

        stage_result = guide_result[stage_key] or {}
        data_context = json.dumps(stage_result, ensure_ascii=False, indent=2)

        section = build_section_via_llm(
            system_prompt=system_prompt,
            data_context=data_context,
            rag_query=f"보존처리 보고서 {title} 과정 서술",
        )
        return {"sections": {stage_key: section.model_dump()}}

    node.__name__ = f"{stage_key}_node"
    return node


disassembly_node = make_guide_stage_node("disassembly")
cleaning_node = make_guide_stage_node("cleaning")
reinforcement_node = make_guide_stage_node("reinforcement")
bonding_node = make_guide_stage_node("bonding")
restoration_node = make_guide_stage_node("restoration")
