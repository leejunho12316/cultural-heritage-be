from langgraph.types import interrupt

from ..state import State, _now, stage_guard, _build_result
from ..schemas import DisassemblyChecklist, ToolRecommendation, DisassemblyMethod
from ..llm import llm

# 해처 노드!!

################################## 함수 모음 ##################################
# 해체 체크리스트 - GPT invoke & 파싱
def _get_disassembly_checklist(relic_info: dict) -> dict:

    structured_llm = llm.with_structured_output(DisassemblyChecklist)

    prompt = f"""당신은 문화재 보존처리 전문가입니다.
아래 유물 정보를 참고해서, 해체 작업을 시작하기 전 반드시 확인해야 할
체크리스트를 만들어주세요.

유물 정보: {relic_info}"""

    result: DisassemblyChecklist = structured_llm.invoke(prompt)
    return result.model_dump()   # 노드/State 는 dict 를 기대하므로 변환
# %%


# 해체 도구 추천 - GPT invoke & 파싱
def _get_recommended_tools(relic_info: dict, ai_checklist: dict, confirmed_checklist: dict) -> dict:
    """사람이 확정한(체크한) 체크리스트 항목을 바탕으로 도구를 추천한다."""
    checked_ids = confirmed_checklist.get("checked_ids", [])
    checked_items = [
        item for item in ai_checklist.get("checklist", [])
        if item["id"] in checked_ids
    ]

    structured_llm = llm.with_structured_output(ToolRecommendation)

    prompt = f"""당신은 문화재 보존처리 전문가입니다.
아래는 해체 작업 전 담당자가 확인(체크)한 항목들입니다.
이 내용을 바탕으로 실제 해체 작업에 사용할 도구를 추천해주세요.
도구 출력시 도구의 이름을 먼저 말하고 설명을 해주세요./
유물 정보: {relic_info}
확인된 체크리스트 항목: {checked_items}"""

    result: ToolRecommendation = structured_llm.invoke(prompt)
    return result.model_dump()


# 해체 방법(단계별 절차) - GPT invoke & 파싱
def _get_disassembly_method(relic_info: dict, confirmed_checklist: dict, confirmed_tools: dict) -> dict:
    """확정된 체크리스트와 확정된 도구를 바탕으로 해체 작업을 단계별로 안내한다."""
    structured_llm = llm.with_structured_output(DisassemblyMethod)

    prompt = f"""당신은 문화재 보존처리 전문가입니다.
아래 유물 정보와, 담당자가 확정한 체크리스트 및 도구를 참고해서
해체 작업을 처음부터 끝까지 수행할 수 있도록 순서대로 단계를 나눠 안내해주세요.
각 단계마다 사용할 도구와 주의할 점도 함께 알려주세요.

유물 정보: {relic_info}
확정된 체크리스트: {confirmed_checklist}
확정된 도구: {confirmed_tools}"""

    result: DisassemblyMethod = structured_llm.invoke(prompt)
    return result.model_dump()

################################## 노드 모음 ##################################
# LLM 호출 + interrupt()를 한 노드에 같이 두면 resume 시 중단됐던 노드를 처음부터 다시 실행하기 때문에 LLM 호출이 중복된다.
# 시간, 비용면에서 굉장히 비효율적이라 노드를 쪼갰다.

# (2-1) 체크리스트 생성 : LLM 호출 1회. interrupt 가 없어 resume 로 인한 재실행 대상이 아니다.
@stage_guard("disassembly")
def disassembly_checklist_node(state: State):
  relic = state.get("relic_info", {})

  ai_checklist = _get_disassembly_checklist(relic)

  return {
    "cur_flow": "disassembly",
    "results": {"disassembly": {"status": "in_progress", "ai_checklist": ai_checklist}},
    "last_edited_date": _now(),
  }


# (2-2) 체크리스트 확인 : interrupt() 만 담당. resume 마다 다시 실행돼도 LLM 호출이 없어 안전하다.
@stage_guard("disassembly")
def disassembly_confirm_checklist_node(state: State):
  ai_checklist = state["results"]["disassembly"]["ai_checklist"]

  # interrupt : FE로 해체 전 확인해야 할 항목 띄우고 사용자 체크 받아오기.
  # + checkpointer가 멈춘 지점을 기억함.
  # + graph.invoke(Command(resume))로 돌아왔을 때 resume값이 confirmed가 됨.
  confirmed_checklist = interrupt({
    "type": "tool_selection",
    "stage": "disassembly",
    "ai_disassembly_checklist": ai_checklist,
    "question" : "아래 항목을 확인하고 체크한 뒤 확정하십시오."
  })

  print('확인된 체크리스트 값 : ', confirmed_checklist)

  return {
    "cur_flow": "disassembly",
        "results": {"disassembly": {"confirmed_checklist": confirmed_checklist}},
    "last_edited_date": _now(),
  }


# (2-3) 도구 추천 생성 : LLM 호출 1회. 확정된 체크리스트를 바탕으로 도구를 추천한다.
@stage_guard("disassembly")
def disassembly_tools_node(state: State):
  relic = state.get("relic_info", {})
  ai_checklist = state["results"]["disassembly"]["ai_checklist"]
  confirmed_checklist = state["results"]["disassembly"]["confirmed_checklist"]

  ai_tools = _get_recommended_tools(relic, ai_checklist, confirmed_checklist)

  return {
    "cur_flow": "disassembly",
    "results": {"disassembly": {"ai_tools": ai_tools}},
    "last_edited_date": _now(),
  }


# (2-4) 도구 확인 : interrupt() 만 담당. resume 마다 다시 실행돼도 LLM 호출이 없어 안전하다.
@stage_guard("disassembly")
def disassembly_confirm_tools_node(state: State):
  ai_tools = state["results"]["disassembly"]["ai_tools"]

  confirmed_tools = interrupt({
    "type": "tool_selection",
    "stage": "disassembly",
    "ai_recommended_tools": ai_tools,
    "question": "추천된 도구를 확인하고 확정하십시오.",
  })

  print('확인된 도구 값 : ', confirmed_tools)

  return {
    "cur_flow": "disassembly",
    "results": {"disassembly": {"confirmed_tools": confirmed_tools}},
    "last_edited_date": _now(),
  }


# (2-5) 해체 방법 생성 : LLM 호출 1회. 확정된 체크리스트/도구를 바탕으로 단계별 절차를 생성한다.
@stage_guard("disassembly")
def disassembly_method_node(state: State):
  relic = state.get("relic_info", {})
  confirmed_checklist = state["results"]["disassembly"]["confirmed_checklist"]
  confirmed_tools = state["results"]["disassembly"]["confirmed_tools"]

  ai_method = _get_disassembly_method(relic, confirmed_checklist, confirmed_tools)

  return {
    "cur_flow": "disassembly",
    "results": {"disassembly": {"ai_method": ai_method}},
    "last_edited_date": _now(),
  }


# (2-6) 해체 방법 확인 : interrupt() 만 담당. FE에서 단계별 작업 완료 여부를 받아 disassembly 단계를 마무리한다.
@stage_guard("disassembly")
def disassembly_confirm_method_node(state: State):
  ai_method = state["results"]["disassembly"]["ai_method"]

  confirmed_method = interrupt({
    "type": "step_check",
    "stage": "disassembly",
    "ai_disassembly_method": ai_method,
    "question": "각 단계별 작업 완료 여부를 체크하십시오.",
  })

  print('확인된 해체 방법 진행 상태 : ', confirmed_method)

  ai_checklist = state["results"]["disassembly"]["ai_checklist"]
  confirmed_checklist = state["results"]["disassembly"]["confirmed_checklist"]
  ai_tools = state["results"]["disassembly"]["ai_tools"]
  confirmed_tools = state["results"]["disassembly"]["confirmed_tools"]

  select_options = {
    "checklist": confirmed_checklist,
    "tools": confirmed_tools,
    "method": confirmed_method,
  }
  ai_reco = {
    "checklist": ai_checklist,
    "tools": ai_tools,
    "method": ai_method,
  }

  return {
    "cur_flow": "disassembly",
    "results": {"disassembly": _build_result(select_options, ai_reco, state.get("task_manager"))},
    "last_edited_date": _now(),
  }
