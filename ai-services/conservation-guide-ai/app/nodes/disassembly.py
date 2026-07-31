from langgraph.types import interrupt

from ..state import State, _now, stage_guard, _build_result, assign_ids
from ..schemas import DisassemblyChecklist, ToolRecommendation, DisassemblyMethod
from ..llm import llm

# 해처 노드!!

################################## 함수 모음 ##################################
# 해체 체크리스트 추천
# 유물 정보 -> 체크리스트
def _get_disassembly_checklist(relic_info: dict) -> dict:

    structured_llm = llm.with_structured_output(DisassemblyChecklist)

    prompt = f"""
    #역할
    당신은 문화재 보존처리 전문가입니다.
    아래 유물 정보를 참고해서, 해체 작업을 시작하기 전 반드시 확인해야 할 체크리스트를 정확히 10개 만들어주세요.
    항목이 부족하거나 넘치지 않도록 비슷한 확인사항은 하나로 묶고, 세부 항목은 나눠서 10개를 채워주세요.

    label, caution은 자연스러운 한국어 문장으로 풀어서 작성하고,
    ·, /, (), {{}} 같은 기호는 최대한 쓰지 마세요.
    예를 들어 "아세톤/에탄올" 대신 "아세톤이나 에탄올", "(농도 20%)" 대신 "농도는 20퍼센트로" 처럼 표현하세요.

    # 정보
    유물 정보: {relic_info}"""

    result: DisassemblyChecklist = structured_llm.invoke(prompt)

    data = result.model_dump()
    data["checklist"] = assign_ids(data["checklist"], "disassembly", "checklist")

    return data   # 노드/State 는 dict 를 기대하므로 변환



# 해체 도구 추천
# 유물 정보, 체크리스트 -> 도구
def _get_recommended_tools(relic_info: dict, ai_checklist: dict, confirmed_checklist: dict) -> dict:

    checked_ids = confirmed_checklist.get("checked_ids", [])
    checked_items = [
        item for item in ai_checklist.get("checklist", [])
        if item["id"] in checked_ids
    ]

    structured_llm = llm.with_structured_output(ToolRecommendation)

    prompt = f"""당신은 문화재 보존처리 전문가입니다.
    아래는 해체 작업 전 담당자가 확인(체크)한 항목들입니다.
    이 내용을 바탕으로 해체 작업에 관련된 도구를 정확히 3개 나열해주세요.
    도구 출력시 도구의 이름을 먼저 말하고 설명을 해주세요.

    지금 상황(유물 재질/상태 등)을 고려했을 때 가장 중요한 도구 1개만 recommended: true로 표시하고,
    나머지 2개는 recommended: false로 표시해주세요. true는 반드시 3개 중 정확히 1개여야 합니다.
    (FE에서 recommended: true인 도구만 기본으로 체크된 상태로 보여줍니다.)

    description, reason, precautions는 자연스러운 한국어 문장으로 풀어서 작성하고,
    ·, /, (), {{}} 같은 기호는 최대한 쓰지 마세요.
    예를 들어 "아세톤/에탄올" 대신 "아세톤이나 에탄올", "(농도 20%)" 대신 "농도는 20퍼센트로" 처럼 표현하세요.

    #정보
    유물 정보: {relic_info}
    확인된 체크리스트 항목: {checked_items}"""

    result: ToolRecommendation = structured_llm.invoke(prompt)

    data = result.model_dump()
    data["recommended_tools"] = assign_ids(data["recommended_tools"], "disassembly", "tools")

    return data


# 해체 방법(단계별 절차)
# 유물 정보, 체크리스트, 도구 -> 방법(단계별 절차)
def _get_disassembly_method(relic_info: dict, ai_checklist: dict, confirmed_checklist: dict, confirmed_tools: dict) -> dict:

    checked_ids = confirmed_checklist.get("checked_ids", [])
    checked_items = [
        item for item in ai_checklist.get("checklist", [])
        if item["id"] in checked_ids
    ]

    structured_llm = llm.with_structured_output(DisassemblyMethod)

    prompt = f"""당신은 문화재 보존처리 전문가입니다.
    아래 유물 정보와, 담당자가 확정한 체크리스트 및 도구를 참고해서
    해체 작업을 처음부터 끝까지 5~6단계로 요약해서 순서대로 안내해주세요.
    세부 동작을 잘게 나누지 말고, 유사하거나 연속된 작업은 하나의 단계로 묶어주세요.
    각 단계마다 사용할 도구와 주의할 점도 함께 알려주세요.

    label, caution, overall_caution은 자연스러운 한국어 문장으로 풀어서 작성하고,
    ·, /, (), {{}} 같은 기호는 최대한 쓰지 마세요.
    예를 들어 "아세톤/에탄올" 대신 "아세톤이나 에탄올", "(농도 20%)" 대신 "농도는 20퍼센트로" 처럼 표현하세요.

    유물 정보: {relic_info}
    확정된 체크리스트: {checked_items}
    확정된 도구: {confirmed_tools}"""

    result: DisassemblyMethod = structured_llm.invoke(prompt)

    data = result.model_dump()
    data["steps"] = assign_ids(data["steps"], "disassembly", "method")

    return data





################################## 노드 모음 ##################################
# LLM 호출 + interrupt()를 한 노드에 같이 두면 resume 시 중단됐던 노드를 처음부터 다시 실행하기 때문에 LLM 호출이 중복된다.
# 시간, 비용면에서 굉장히 비효율적이라 노드를 쪼갰다.

# (2-1) 체크리스트 생성 : LLM 호출 1회. interrupt 가 없어 resume 로 인한 재실행 대상이 아니다.
@stage_guard("disassembly")
def disassembly_checklist_node(state: State):
  relic_info = state.get("relic_info", {})

  ai_checklist = _get_disassembly_checklist(relic_info)

  return {
    "cur_flow": "disassembly",
    "results": {
        "disassembly":
            {"status": "in_progress",
             "ai_checklist": ai_checklist}
    },
    "last_edited_date": _now(),
  }


# (2-1) 체크리스트 확인 : interrupt() 만 담당. resume 마다 다시 실행돼도 LLM 호출이 없어 안전하다.
# interrupt : FE로 해체 전 확인해야 할 항목 띄우고 사용자 체크 받아오기.
# + checkpointer가 멈춘 지점을 기억함.
# + graph.invoke(Command(resume))로 돌아왔을 때 resume값이 confirmed가 됨.
@stage_guard("disassembly")
def disassembly_confirm_checklist_node(state: State):
  ai_checklist = state["results"]["disassembly"]["ai_checklist"]

  # FE input 형식 : {"checked_ids" : [str]}
  confirmed_checklist = interrupt({
    "stage": "해체 - 체크리스트를 선택하세요!",
    "ai_checklist": ai_checklist,
  })

  return {
    "cur_flow": "disassembly",
        "results": {
            "disassembly": {
                "confirmed_checklist": confirmed_checklist}
        },
    "last_edited_date": _now(),
  }


# (2-2) 도구 추천 생성 : LLM 호출 1회. 확정된 체크리스트를 바탕으로 도구를 추천한다.
@stage_guard("disassembly")
def disassembly_tools_node(state: State):
  # 유물 정보, 체크리스트, 선택한 체크리스트
  relic_info = state.get("relic_info", {})
  ai_checklist = state["results"]["disassembly"]["ai_checklist"]
  confirmed_checklist = state["results"]["disassembly"]["confirmed_checklist"]

  ai_tools = _get_recommended_tools(relic_info, ai_checklist, confirmed_checklist)

  return {
    "cur_flow": "disassembly",
    "results": {
        "disassembly": {"ai_tools": ai_tools}
    },
    "last_edited_date": _now(),
  }


# (2-2) 도구 확인 : interrupt() 만 담당. resume 마다 다시 실행돼도 LLM 호출이 없어 안전하다.
@stage_guard("disassembly")
def disassembly_confirm_tools_node(state: State):
  ai_tools = state["results"]["disassembly"]["ai_tools"]

  # FE input 형식 : {"confirmed_tools" : [str]}
  confirmed_tools = interrupt({
    "stage": "해체 - 도구를 선택하세요!",
    "ai_tools": ai_tools
  })

  return {
    "cur_flow": "disassembly",
    "results": {
        "disassembly": {"confirmed_tools": confirmed_tools}},
    "last_edited_date": _now(),
  }


# (2-3) 해체 방법 생성 : LLM 호출 1회. 확정된 체크리스트/도구를 바탕으로 단계별 절차를 생성한다.
@stage_guard("disassembly")
def disassembly_method_node(state: State):
  relic = state.get("relic_info", {})
  ai_checklist = state["results"]["disassembly"]["ai_checklist"]
  confirmed_checklist = state["results"]["disassembly"]["confirmed_checklist"]
  confirmed_tools = state["results"]["disassembly"]["confirmed_tools"]

  ai_method = _get_disassembly_method(relic, ai_checklist, confirmed_checklist, confirmed_tools)

  return {
    "cur_flow": "disassembly",
    "results": {"disassembly": {"ai_method": ai_method}},
    "last_edited_date": _now(),
  }


# (2-3) 해체 방법 확인 : interrupt() 만 담당. resume 마다 다시 실행돼도 LLM 호출이 없어 안전하다.
@stage_guard("disassembly")
def disassembly_confirm_method_node(state: State):
  ai_method = state["results"]["disassembly"]["ai_method"]

  # FE input 형식 : {"completed_step_ids" : [str]}
  confirmed_method = interrupt({
    "stage": "해체 - 단계별 작업 완료 여부 체크!",
    "ai_disassembly_method": ai_method
  })

  return {
    "cur_flow": "disassembly",
    "results": {
        "disassembly": {"confirmed_method": confirmed_method}},
    "last_edited_date": _now(),
  }


# (2-4) 해체 단계 총정리 : interrupt 없음. LLM 호출도 없이 지금까지의 결과를 모아 disassembly 단계를 완료 처리한다.
@stage_guard("disassembly")
def disassembly_end(state: State):

    # FE input 형식 : {"photo_urls" : [str], "memo" : str}
    disassembly_wrapup = interrupt({
        "stage": "해체 - 마지막 단계. 작업 후 사진/메모를 입력해주세요."
    })

    return {
        "cur_flow": "disassembly",
        "results": {"disassembly": _build_result(disassembly_wrapup.get('photo_urls',[]),
                                                 disassembly_wrapup.get('memo', ''),
                                                 state.get("task_manager"))},
        "last_edited_date": _now(),
    }


