from langgraph.types import interrupt

from ..state import State, _now, stage_guard, _build_result, assign_ids
from ..schemas import RestorationMaterialRecommendation, RestorationGuide
from ..llm import llm

# 복원 노드!!

################################## 함수 모음 ##################################
# 복원 재료(합성수지) 추천
def _get_restoration_material(relic_info: dict, confirmed_adhesive: dict) -> dict:

    structured_llm = llm.with_structured_output(RestorationMaterialRecommendation)

    prompt = f"""당신은 문화재 보존처리 전문가입니다.
    아래 정보를 참고해서 복원(결손 부위 메움)에 사용할 합성수지 재료를 추천하고 이유를 제시해주세요.

    복원 재료는 CDK-520, Araldite SV427+HV427, Epo-tec 301, XTR-311, Repairit Quik 중에서 고르세요.

    #정보
    유물 정보: {relic_info}
    접합 단계에서 확정된 접착제: {confirmed_adhesive}"""

    result: RestorationMaterialRecommendation = structured_llm.invoke(prompt)

    return result.model_dump()


# 복원 방법 단계별 안내
def _get_restoration_guide(relic_info: dict, confirmed_material: dict) -> dict:

    structured_llm = llm.with_structured_output(RestorationGuide)

    prompt = f"""당신은 문화재 보존처리 전문가입니다.
    아래 정보와 확정된 복원 재료를 참고해서 복원 작업을 처음부터 끝까지 순서대로 단계별로 안내해주세요.

    #정보
    유물 정보: {relic_info}
    확정된 복원 재료: {confirmed_material}"""

    result: RestorationGuide = structured_llm.invoke(prompt)

    data = result.model_dump()
    data["steps"] = assign_ids(data["steps"], "restoration", "guide")

    return data




################################## 노드 모음 ##################################

# (4-1) 복원 재료 추천 : LLM 호출 1회.
@stage_guard("restoration")
def restoration_material_node(state: State):
  relic_info = state.get("relic_info", {})
  confirmed_adhesive = state.get("results", {}).get("bonding", {}).get("confirmed_adhesive", {})

  ai_material = _get_restoration_material(relic_info, confirmed_adhesive)

  return {
    "cur_flow": "restoration",
    "results": {
        "restoration":
            {"status": "in_progress",
             "ai_material": ai_material}
    },
    "last_edited_date": _now(),
  }


# (4-1) 복원 재료 확인 : interrupt() 만 담당.
# FE input 형식 : {"material": str}
@stage_guard("restoration")
def restoration_confirm_material_node(state: State):
  ai_material = state["results"]["restoration"]["ai_material"]

  confirmed_material = interrupt({
    "stage": "복원 - 복원 재료를 선택하세요!",
    "ai_material": ai_material,
  })

  return {
    "cur_flow": "restoration",
    "results": {
        "restoration": {"confirmed_material": confirmed_material}},
    "last_edited_date": _now(),
  }


# (4-2) 복원 방법 생성 : LLM 호출 1회.
@stage_guard("restoration")
def restoration_guide_node(state: State):
  relic_info = state.get("relic_info", {})
  confirmed_material = state["results"]["restoration"]["confirmed_material"]

  ai_guide = _get_restoration_guide(relic_info, confirmed_material)

  return {
    "cur_flow": "restoration",
    "results": {"restoration": {"ai_guide": ai_guide}},
    "last_edited_date": _now(),
  }


# (4-2) 복원 방법 완료 확인 : interrupt() 만 담당.
# FE input 형식 : {"completed_step_ids": [str]}
@stage_guard("restoration")
def restoration_confirm_guide_node(state: State):
  ai_guide = state["results"]["restoration"]["ai_guide"]

  confirmed_guide = interrupt({
    "stage": "복원 - 단계별 작업 완료 여부 체크!",
    "ai_guide": ai_guide,
  })

  return {
    "cur_flow": "restoration",
    "results": {
        "restoration": {"confirmed_guide": confirmed_guide}},
    "last_edited_date": _now(),
  }


# (4-3) 복원 단계 총정리 : interrupt 만 담당.
# FE input 형식 : {"photo_urls": [str], "memo": str}
@stage_guard("restoration")
def restoration_end(state: State):

    restoration_wrapup = interrupt({
        "stage": "복원 - 마지막 단계. 작업 후 사진/메모를 입력해주세요."
    })

    return {
        "cur_flow": "restoration",
        "results": {"restoration": _build_result(restoration_wrapup.get('photo_urls', []),
                                                   restoration_wrapup.get('memo', ''),
                                                   state.get("task_manager"))},
        "last_edited_date": _now(),
    }
