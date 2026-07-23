from langgraph.types import interrupt

from ..state import State, _now, stage_guard, _build_result, assign_ids
from ..schemas import CleaningAnalysis, CleaningGuide, DryingGuide
from ..llm import llm

# 세척 노드!!

################################## 함수 모음 ##################################
# 유물 상태/오염물 분석 + 물리적/화학적 세척 필요여부 판단
def _get_cleaning_analysis(relic_info: dict) -> dict:

    structured_llm = llm.with_structured_output(CleaningAnalysis)

    prompt = f"""
    #역할
    당신은 문화재 보존처리 전문가입니다.
    아래 유물 정보를 참고해서 유물의 상태와 오염물의 종류를 요약하고,
    물리적 세척과 화학적 세척 중 어떤 것이 필요한지 분석해주세요.
    먼지/흙같이 쉽게 제거 가능한 오염물은 물리적 세척으로,
    그 외 제거하기 힘든 오염물은 화학적 세척이 필요합니다.

    # 정보
    유물 정보: {relic_info}"""

    result: CleaningAnalysis = structured_llm.invoke(prompt)

    return result.model_dump()


# 확정된 세척법에 따른 단계별 안내 (체크되지 않은 세척법은 안내하지 않음)
def _get_cleaning_guide(relic_info: dict, ai_analysis: dict, confirmed_method: dict) -> dict:

    use_physical = confirmed_method.get("use_physical", False)
    use_chemical = confirmed_method.get("use_chemical", False)

    structured_llm = llm.with_structured_output(CleaningGuide)

    prompt = f"""당신은 문화재 보존처리 전문가입니다.
    아래 유물 정보와 세척 분석 결과를 참고해서 세척 작업을 단계별로 안내해주세요.

    물리적 세척 진행 여부: {use_physical}
    화학적 세척 진행 여부: {use_chemical}
    (진행하지 않는 세척법은 절대 안내하지 마세요. 둘 다 진행하는 경우 보통 물리적 세척 후 화학적 세척 순서로 안내하세요.)

    물리적 세척 참고사항: 오염물 강도에 따라 작은 강도의 도구부터 큰 강도의 도구로 바꿔가며 진행합니다.
    약한 연질토기는 Paraloid B72를 2~5% 농도로 용해시킨 용액을 스프레이로 뿌려 표면을 임시로 강화시킨 후 조금씩 제거하고,
    단단한 이물질은 초음파세척기/스팀세척기/에어브레시브 등을 사용합니다.
    화학적 세척 참고사항: 침지분산법(물/유기용제에 침적)과 습포법(용제를 흡수시킨 습포물질을 오염부에 부착)이 있습니다.

    #정보
    유물 정보: {relic_info}
    세척 분석 결과: {ai_analysis}"""

    result: CleaningGuide = structured_llm.invoke(prompt)

    data = result.model_dump()
    data["steps"] = assign_ids(data["steps"], "cleaning", "guide")

    return data


# 재질에 따른 건조 안내
def _get_drying_guide(relic_info: dict) -> dict:

    structured_llm = llm.with_structured_output(DryingGuide)

    prompt = f"""당신은 문화재 보존처리 전문가입니다.
    아래 유물 정보(특히 재질)를 참고해서 세척 후 건조 방식을 단계별로 안내해주세요.

    참고사항: 세척이 끝난 도자기는 상온에서 자연건조 하는 것이 좋습니다.
    연질토기는 수축/휨/균열 등 파손 위험이 있어 서늘한 실내에서 서서히 건조시키는 것이 중요합니다(건조 환경 조건이 중요).
    경질토기/도자기는 상온 자연건조, 혹은 처리시간 단축 등의 이유로 열풍건조기에서 섭씨 50도 이하로 건조하기도 합니다.

    #정보
    유물 정보: {relic_info}"""

    result: DryingGuide = structured_llm.invoke(prompt)

    data = result.model_dump()
    data["steps"] = assign_ids(data["steps"], "cleaning", "drying")

    return data




################################## 노드 모음 ##################################

# (1-1) 세척법 분석 : LLM 호출 1회.
@stage_guard("cleaning")
def cleaning_analysis_node(state: State):
  relic_info = state.get("relic_info", {})

  ai_analysis = _get_cleaning_analysis(relic_info)

  return {
    "cur_flow": "cleaning",
    "results": {
        "cleaning":
            {"status": "in_progress",
             "ai_analysis": ai_analysis}
    },
    "last_edited_date": _now(),
  }


# (1-1) 세척법 확인 : interrupt() 만 담당.
# FE input 형식 : {"use_physical": bool, "use_chemical": bool}
@stage_guard("cleaning")
def cleaning_confirm_method_node(state: State):
  ai_analysis = state["results"]["cleaning"]["ai_analysis"]

  confirmed_method = interrupt({
    "stage": "세척 - 진행할 세척법을 선택하세요!",
    "ai_analysis": ai_analysis,
  })

  return {
    "cur_flow": "cleaning",
    "results": {
        "cleaning": {"confirmed_method": confirmed_method}},
    "last_edited_date": _now(),
  }


# (1-2) 세척법 안내 생성 : LLM 호출 1회.
@stage_guard("cleaning")
def cleaning_guide_node(state: State):
  relic_info = state.get("relic_info", {})
  ai_analysis = state["results"]["cleaning"]["ai_analysis"]
  confirmed_method = state["results"]["cleaning"]["confirmed_method"]

  ai_guide = _get_cleaning_guide(relic_info, ai_analysis, confirmed_method)

  return {
    "cur_flow": "cleaning",
    "results": {"cleaning": {"ai_guide": ai_guide}},
    "last_edited_date": _now(),
  }


# (1-2) 세척법 안내 완료 확인 : interrupt() 만 담당.
# FE input 형식 : {"completed_step_ids": [str]}
@stage_guard("cleaning")
def cleaning_confirm_guide_node(state: State):
  ai_guide = state["results"]["cleaning"]["ai_guide"]

  confirmed_guide = interrupt({
    "stage": "세척 - 단계별 작업 완료 여부 체크!",
    "ai_guide": ai_guide,
  })

  return {
    "cur_flow": "cleaning",
    "results": {
        "cleaning": {"confirmed_guide": confirmed_guide}},
    "last_edited_date": _now(),
  }


# (1-3) 건조 안내 생성 : LLM 호출 1회.
@stage_guard("cleaning")
def cleaning_drying_guide_node(state: State):
  relic_info = state.get("relic_info", {})

  ai_drying_guide = _get_drying_guide(relic_info)

  return {
    "cur_flow": "cleaning",
    "results": {"cleaning": {"ai_drying_guide": ai_drying_guide}},
    "last_edited_date": _now(),
  }


# (1-3) 건조 완료 확인 : interrupt() 만 담당.
# FE input 형식 : {"completed_step_ids": [str]}
@stage_guard("cleaning")
def cleaning_confirm_drying_node(state: State):
  ai_drying_guide = state["results"]["cleaning"]["ai_drying_guide"]

  confirmed_drying = interrupt({
    "stage": "세척 - 건조 완료 여부 체크!",
    "ai_drying_guide": ai_drying_guide,
  })

  return {
    "cur_flow": "cleaning",
    "results": {
        "cleaning": {"confirmed_drying": confirmed_drying}},
    "last_edited_date": _now(),
  }


# (1-4) 세척 단계 총정리 : interrupt 만 담당.
# FE input 형식 : {"photo_urls": [str], "memo": str}
@stage_guard("cleaning")
def cleaning_end(state: State):

    cleaning_wrapup = interrupt({
        "stage": "세척 - 마지막 단계. 작업 후 사진/메모를 입력해주세요."
    })

    return {
        "cur_flow": "cleaning",
        "results": {"cleaning": _build_result(cleaning_wrapup.get('photo_urls', []),
                                               cleaning_wrapup.get('memo', ''),
                                               state.get("task_manager"))},
        "last_edited_date": _now(),
    }
