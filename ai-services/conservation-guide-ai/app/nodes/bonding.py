from langgraph.types import interrupt

from ..state import State, _now, stage_guard, _build_result, assign_ids
from ..schemas import BondingAdhesiveRecommendation, BondingMethodRecommendation
from ..llm import llm

# 접합 노드!!

################################## 함수 모음 ##################################
# 접착제 추천
def _get_adhesive_recommendation(relic_info: dict, reinforcement_agent: dict) -> dict:

    structured_llm = llm.with_structured_output(BondingAdhesiveRecommendation)

    prompt = f"""당신은 문화재 보존처리 전문가입니다.
    아래 정보를 참고해서 접합에 사용할 접착제를 추천하고 이유를 제시해주세요.

    접착제는 Paraloid B-72, Cemedine C, Araldite rapid, Cyanoacrylate, poly urethane, Loctite 401 중에서 고르세요.
    다음 사항을 반드시 고려하세요.
    - 유물 재질이 연질토기인 경우 점도가 낮은 순간접착제(Cyanoacrylate)는 사용할 수 없습니다.
    - 처리 목적이 전시용이면 나중에 다시 분리/재처리할 수 있도록 가역성이 높은 접착제를, 수장/연구용이면 강도를 우선시하는 접착제를 추천하세요.
    - 강화처리 단계에서 사용한 강화제와 상성이 좋은 접착제를 추천하세요.

    #정보
    유물 정보(무게/접합면 면적/재질/처리목적 포함): {relic_info}
    강화처리 단계에서 확정된 강화제/용매: {reinforcement_agent}"""

    result: BondingAdhesiveRecommendation = structured_llm.invoke(prompt)

    return result.model_dump()


# 접합 방식(복합/단일/결합/모세관접합) + 단계별 안내
def _get_bonding_method(relic_info: dict, confirmed_adhesive: dict) -> dict:

    structured_llm = llm.with_structured_output(BondingMethodRecommendation)

    prompt = f"""당신은 문화재 보존처리 전문가입니다.
    아래 정보를 참고해서 복합접합, 단일접합, 결합접합, 모세관접합 중 적절한 접합 방식을 추천하고,
    확정된 접착제를 사용해 접합 작업을 처음부터 끝까지 순서대로 단계별로 안내해주세요.

    #정보
    유물 정보: {relic_info}
    확정된 접착제: {confirmed_adhesive}"""

    result: BondingMethodRecommendation = structured_llm.invoke(prompt)

    data = result.model_dump()
    data["steps"] = assign_ids(data["steps"], "bonding", "method")

    return data




################################## 노드 모음 ##################################

# (3-1) 접착제 추천 : LLM 호출 1회.
@stage_guard("bonding")
def bonding_adhesive_node(state: State):
  relic_info = state.get("relic_info", {})
  reinforcement_agent = state.get("results", {}).get("reinforcement", {}).get("confirmed_agent", {})

  ai_adhesive = _get_adhesive_recommendation(relic_info, reinforcement_agent)

  return {
    "cur_flow": "bonding",
    "results": {
        "bonding":
            {"status": "in_progress",
             "ai_adhesive": ai_adhesive}
    },
    "last_edited_date": _now(),
  }


# (3-1) 접착제 확인 : interrupt() 만 담당.
# FE input 형식 : {"adhesive": str}
@stage_guard("bonding")
def bonding_confirm_adhesive_node(state: State):
  ai_adhesive = state["results"]["bonding"]["ai_adhesive"]

  confirmed_adhesive = interrupt({
    "stage": "접합 - 접착제를 선택하세요!",
    "ai_adhesive": ai_adhesive,
  })

  return {
    "cur_flow": "bonding",
    "results": {
        "bonding": {"confirmed_adhesive": confirmed_adhesive}},
    "last_edited_date": _now(),
  }


# (3-2) 임시접합 전/후 사진 입력 : interrupt() 만 담당. LLM 없음.
# FE input 형식 : {"before_photo_urls": [str], "after_photo_urls": [str]}
@stage_guard("bonding")
def bonding_temp_node(state: State):
  temp_bonding = interrupt({
    "stage": "접합 - 임시접합 전/후 사진을 입력하세요!",
  })

  return {
    "cur_flow": "bonding",
    "results": {
        "bonding": {"temp_bonding": temp_bonding}},
    "last_edited_date": _now(),
  }


# (3-3) 접합 방법 생성 : LLM 호출 1회.
@stage_guard("bonding")
def bonding_method_node(state: State):
  relic_info = state.get("relic_info", {})
  confirmed_adhesive = state["results"]["bonding"]["confirmed_adhesive"]

  ai_method = _get_bonding_method(relic_info, confirmed_adhesive)

  return {
    "cur_flow": "bonding",
    "results": {"bonding": {"ai_method": ai_method}},
    "last_edited_date": _now(),
  }


# (3-3) 접합 방법 완료 확인 : interrupt() 만 담당.
# FE input 형식 : {"completed_step_ids": [str]}
@stage_guard("bonding")
def bonding_confirm_method_node(state: State):
  ai_method = state["results"]["bonding"]["ai_method"]

  confirmed_method = interrupt({
    "stage": "접합 - 단계별 작업 완료 여부 체크!",
    "ai_method": ai_method,
  })

  return {
    "cur_flow": "bonding",
    "results": {
        "bonding": {"confirmed_method": confirmed_method}},
    "last_edited_date": _now(),
  }


# (3-4) 접합 단계 총정리 : interrupt 만 담당.
# FE input 형식 : {"photo_urls": [str], "memo": str}
@stage_guard("bonding")
def bonding_end(state: State):

    bonding_wrapup = interrupt({
        "stage": "접합 - 마지막 단계. 작업 후 사진/메모를 입력해주세요."
    })

    return {
        "cur_flow": "bonding",
        "results": {"bonding": _build_result(bonding_wrapup.get('photo_urls', []),
                                              bonding_wrapup.get('memo', ''),
                                              state.get("task_manager"))},
        "last_edited_date": _now(),
    }
