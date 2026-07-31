import base64
import mimetypes

from langchain_core.messages import HumanMessage
from langgraph.types import interrupt

from ..state import State, _now, stage_guard, _build_result, assign_ids
from ..schemas import ReinforcementAgentRecommendation, ColorChangeAnalysis, ReinforcementMethod
from ..llm import llm, vision_llm
from ..reinforcement_rag.rag import retrieve_reference_context

# 강화처리 노드!!

################################## 함수 모음 ##################################
# http(s) URL은 그대로, 로컬 파일 경로는 base64 data URI로 변환해서 vision 모델에 전달.
# (테스트용으로 공개 URL 없이 컨테이너 안의 로컬 이미지 파일로도 검증할 수 있도록)
def _to_image_content(path_or_url: str) -> dict:
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return {"type": "image_url", "image_url": {"url": path_or_url}}

    mime_type, _ = mimetypes.guess_type(path_or_url)
    mime_type = mime_type or "image/png"
    with open(path_or_url, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    return {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}}

# 강화제 + 유기용매 추천
def _get_agent_solvent_recommendation(relic_info: dict) -> dict:

    structured_llm = llm.with_structured_output(ReinforcementAgentRecommendation)

    reference_chunks = retrieve_reference_context(
        f"유물 정보 {relic_info}에 적합한 강화처리용 강화제와 유기용매 추천"
    )
    reference_text = "\n\n".join(
        f"[출처: {chunk['source']} p.{chunk['page']}]\n{chunk['content']}"
        for chunk in reference_chunks
    )

    prompt = f"""당신은 문화재 보존처리 전문가입니다.
    아래 유물 정보를 참고해서 강화처리에 사용할 강화제와, 그 강화제를 희석할 유기용매를 추천하고 이유를 제시해주세요.

    강화제는 Paraloid B72, HPC, 폴리비닐부티랄, 수용성 Emulsion, Paraloid NAD-10 중에서 고르세요.
    각 강화제별 대표/사용가능/비권장 용매는 다음과 같습니다.
    - Paraloid B-72(아크릴계): 대표 용매 아세톤. 사용가능 톨루엔/자일렌/에틸아세테이트/이소프로판올/에탄올/MEK/아밀아세테이트. 비권장 화이트스피릿/나프타.
    - HPC: 대표 용매 에탄올 또는 물. 사용가능 메탄올/이소프로판올/아세톤. 비권장 뜨거운물/벤젠/에테르/나프타/화이트스피릿.
    - 폴리비닐부티랄: 대표 용매 에탄올. 사용가능 알코올류/아세톤/방향족탄화수소(톨루엔 등). 비권장 물.
    - 수용성 에멀전: 대표 용매 물. 비권장 아세톤/톨루엔.
    - Paraloid NAD-10: 대표 용매 나프타. 사용가능 화이트스피릿. 비권장 물/극성용매.

    reason은 자연스러운 한국어 문장으로 풀어서 작성하고,
    ·, /, (), {{}} 같은 기호는 최대한 쓰지 마세요.
    예를 들어 "아세톤/에탄올" 대신 "아세톤이나 에탄올", "(농도 20%)" 대신 "농도는 20퍼센트로" 처럼 표현하세요.
    참고 문헌에 이런 기호가 있어도 그대로 옮기지 말고 문장으로 바꿔서 작성하세요.

    아래는 보존처리 참고 문헌에서 검색된 관련 내용입니다. 위 규칙과 상충하지 않는 범위에서 참고하세요.
    
    #참고 문헌
    {reference_text}

    #정보
    유물 정보: {relic_info}"""

    print("====================prompt====================")
    print(prompt)

    result: ReinforcementAgentRecommendation = structured_llm.invoke(prompt)

    return result.model_dump()


# 습윤 효과 테스트 - 이전/이후 사진 기반 색 변화 분석 (VLM)
def _get_color_change_analysis(before_photo_urls: list, after_photo_urls: list) -> dict:

    structured_vlm = vision_llm.with_structured_output(ColorChangeAnalysis)

    content = [{
        "type": "text",
        "text": """당신은 문화재 보존처리 전문가입니다.
        강화처리 습윤 효과 테스트의 전/후 사진을 비교해서, 토기 표면에 나타난 변화를 아래 9개 항목별로 각각 분석해주세요.
        각 항목은 반드시 severity(none/mild/moderate/severe)와 description을 채워야 하며, 변화가 없으면 severity를 "none"으로 표시하세요.

        1. hue_shift (색상 변화): 색상(색조) 자체가 다른 색으로 옮겨갔는지. 토기 색 자체가 중요한 정보이므로 심하게 변하면 안 됩니다.
        2. brightness_change (명도 변화): 전체적으로 어두워지거나 밝아졌는지.
        3. saturation_change (채도 변화): 색이 더 선명해지거나 탁해졌는지.
        4. gloss_change (광택 변화): 무광이던 표면이 강화제 수지막 때문에 유광/광택이 도는 것으로 바뀌었는지.
        5. blanching (백화현상): 용제가 증발하면서 표면이 하얗게 뜨는 현상이 나타났는지.
        6. uneven_penetration (얼룩/불균일 침투): 강화제가 고르게 스며들지 않아 얼룩이나 경계 자국(tide-line)이 생겼는지.
        7. edge_visibility (처리 경계 뚜렷함): 처리한 부위와 처리하지 않은 부위의 경계선이 도드라져 보이는지. 경계는 자연스럽게 섞여야 이상적입니다.
        8. crack_response (균열부 반응): 균열이나 틈에 강화제가 고이거나, 그 부분만 유독 진해지거나 하얘지는지.
        9. texture_change (질감 변화): 표면의 거칠기/매끄러움 등 촉감상 변화가 있는지.

        위 9개 항목을 종합해서 overall_severity(mild/moderate/severe)를 판정하고,
        moderate 이상인 경우 강화제 수지 변경, 강화제 농도 낮추기, 희석제(용제) 변경 중 적절한 개선 방향을 recommendation에 제안해주세요."""
    }]
    for url in before_photo_urls:
        content.append({"type": "text", "text": "[테스트 전 사진]"})
        content.append(_to_image_content(url))
    for url in after_photo_urls:
        content.append({"type": "text", "text": "[테스트 후 사진]"})
        content.append(_to_image_content(url))

    result: ColorChangeAnalysis = structured_vlm.invoke([HumanMessage(content=content)])

    return result.model_dump()


# 강화 처리 방법(분무법/침지법) 안내
def _get_reinforcement_method(relic_info: dict, confirmed_agent: dict) -> dict:

    structured_llm = llm.with_structured_output(ReinforcementMethod)

    reference_chunks = retrieve_reference_context(
        f"유물 정보 {relic_info}, 확정된 강화제/용매 {confirmed_agent}에 대한 "
        f"강화처리 분무법/침지법 작업 순서(플로우)와 각 단계별 주의사항"
    )
    reference_text = "\n\n".join(
        f"[출처: {chunk['source']} p.{chunk['page']}]\n{chunk['content']}"
        for chunk in reference_chunks
    )

    prompt = f"""당신은 문화재 보존처리 전문가입니다.
    아래 유물 정보와 확정된 강화제/용매를 참고해서 분무법 또는 침지법 중 적절한 방법을 추천하고,
    강화 처리 작업을 처음부터 끝까지 5~6단계로 요약해서 순서대로 안내해주세요.
    세부 동작을 잘게 나누지 말고, 유사하거나 연속된 작업은 하나의 단계로 묶어주세요.

    label, caution, overall_caution은 자연스러운 한국어 문장으로 풀어서 작성하고,
    ·, /, (), {{}} 같은 기호는 최대한 쓰지 마세요.
    예를 들어 "아세톤/에탄올" 대신 "아세톤이나 에탄올", "(농도 20%)" 대신 "농도는 20퍼센트로" 처럼 표현하세요.
    참고 문헌에 이런 기호가 있어도 그대로 옮기지 말고 문장으로 바꿔서 작성하세요.

    아래는 보존처리 참고 문헌에서 검색된 분무법/침지법 관련 내용입니다.
    실제 현장 절차와 주의사항을 반영하되, 문헌에 없는 내용을 있는 것처럼 단정하지 마세요.

    #참고 문헌
    {reference_text}

    #정보
    유물 정보: {relic_info}
    확정된 강화제/용매: {confirmed_agent}"""

    result: ReinforcementMethod = structured_llm.invoke(prompt)

    data = result.model_dump()
    data["steps"] = assign_ids(data["steps"], "reinforcement", "method")

    return data




################################## 노드 모음 ##################################

# (2-1) 강화제/용매 추천 : LLM 호출 1회.
@stage_guard("reinforcement")
def reinforcement_agent_solvent_node(state: State):
  relic_info = state.get("relic_info", {})

  ai_recommendation = _get_agent_solvent_recommendation(relic_info)

  return {
    "cur_flow": "reinforcement",
    "results": {
        "reinforcement":
            {"status": "in_progress",
             "ai_recommendation": ai_recommendation}
    },
    "last_edited_date": _now(),
  }


# (2-1) 강화제/용매 확인 : interrupt() 만 담당.
# FE input 형식 : {"agent": str, "solvent": str}
@stage_guard("reinforcement")
def reinforcement_confirm_agent_node(state: State):
  ai_recommendation = state["results"]["reinforcement"]["ai_recommendation"]

  confirmed_agent = interrupt({
    "stage": "강화처리 - 강화제와 유기용매를 선택하세요!",
    "ai_recommendation": ai_recommendation,
  })

  return {
    "cur_flow": "reinforcement",
    "results": {
        "reinforcement": {"confirmed_agent": confirmed_agent}},
    "last_edited_date": _now(),
  }


# (2-2) 습윤 효과 테스트용 전/후 사진 입력 : interrupt() 만 담당.
# FE input 형식 : {"before_photo_urls": [str], "after_photo_urls": [str]}
@stage_guard("reinforcement")
def reinforcement_wetting_photos_node(state: State):
  wetting_photos = interrupt({
    "stage": "강화처리 - 습윤 효과 테스트용 전/후 사진을 입력하세요!",
  })

  return {
    "cur_flow": "reinforcement",
    "results": {
        "reinforcement": {"wetting_test_photos": wetting_photos}},
    "last_edited_date": _now(),
  }


# (2-2) 습윤 효과(색 변화) 분석 : VLM 호출 1회.
@stage_guard("reinforcement")
def reinforcement_wetting_test_node(state: State):
  wetting_photos = state["results"]["reinforcement"]["wetting_test_photos"]

  ai_color_analysis = _get_color_change_analysis(
      wetting_photos.get("before_photo_urls", []),
      wetting_photos.get("after_photo_urls", []),
  )

  return {
    "cur_flow": "reinforcement",
    "results": {
        "reinforcement": {"ai_color_analysis": ai_color_analysis}},
    "last_edited_date": _now(),
  }


# (2-2) 습윤 효과 테스트 결과 확인 : interrupt() 만 담당.
# FE input 형식 : {"action": "proceed" 또는 "retry"}
@stage_guard("reinforcement")
def reinforcement_confirm_wetting_test_node(state: State):
  ai_color_analysis = state["results"]["reinforcement"]["ai_color_analysis"]

  confirmed_wetting_test = interrupt({
    "stage": "강화처리 - 색 변화 분석 결과를 확인하고 진행 여부를 선택하세요!",
    "ai_color_analysis": ai_color_analysis,
    "default_action": "proceed",
  })

  return {
    "cur_flow": "reinforcement",
    "results": {
        "reinforcement": {"confirmed_wetting_test": confirmed_wetting_test}},
    "last_edited_date": _now(),
  }


# 습윤 효과 테스트 결과에 따른 분기 : "retry" 면 2-1(강화제/용매 재선택)로, "proceed" 면 2-3(처리방법)으로.
# reinforcement 단계 자체가 flow에 없어 stage_guard에 의해 스킵된 경우 confirmed_wetting_test가
# 아예 존재하지 않을 수 있으므로, 그 경우는 그냥 다음 단계로 진행시킨다.
def route_after_wetting_test(state: State) -> str:
  confirmed_wetting_test = state.get("results", {}).get("reinforcement", {}).get("confirmed_wetting_test")
  if not confirmed_wetting_test:
      return "proceed"
  return "retry" if confirmed_wetting_test.get("action") == "retry" else "proceed"


# (2-3) 강화 처리 방법 생성 : LLM 호출 1회.
@stage_guard("reinforcement")
def reinforcement_method_node(state: State):
  relic_info = state.get("relic_info", {})
  confirmed_agent = state["results"]["reinforcement"]["confirmed_agent"]

  ai_method = _get_reinforcement_method(relic_info, confirmed_agent)

  return {
    "cur_flow": "reinforcement",
    "results": {"reinforcement": {"ai_method": ai_method}},
    "last_edited_date": _now(),
  }


# (2-3) 강화 처리 방법 완료 확인 : interrupt() 만 담당.
# FE input 형식 : {"completed_step_ids": [str]}
@stage_guard("reinforcement")
def reinforcement_confirm_method_node(state: State):
  ai_method = state["results"]["reinforcement"]["ai_method"]

  confirmed_method = interrupt({
    "stage": "강화처리 - 단계별 작업 완료 여부 체크!",
    "ai_method": ai_method,
  })

  return {
    "cur_flow": "reinforcement",
    "results": {
        "reinforcement": {"confirmed_method": confirmed_method}},
    "last_edited_date": _now(),
  }


# (2-4) 건조 시작 : LLM/interrupt 없음. 건조 시작 시간만 기록하고 바로 다음으로 진행.
# 2일 이상 상온 자연 건조 (FE가 타이머 표시를 담당, BE는 시작 시간만 저장).
@stage_guard("reinforcement")
def reinforcement_dry_start_node(state: State):
  return {
    "cur_flow": "reinforcement",
    "results": {"reinforcement": {"dry_start_time": _now()}},
    "last_edited_date": _now(),
  }


# (2-5) 강화처리 단계 총정리 : interrupt 만 담당.
# FE input 형식 : {"photo_urls": [str], "memo": str}
@stage_guard("reinforcement")
def reinforcement_end(state: State):

    reinforcement_wrapup = interrupt({
        "stage": "강화처리 - 마지막 단계. 작업 후 사진/메모를 입력해주세요."
    })

    return {
        "cur_flow": "reinforcement",
        "results": {"reinforcement": _build_result(reinforcement_wrapup.get('photo_urls', []),
                                                     reinforcement_wrapup.get('memo', ''),
                                                     state.get("task_manager"))},
        "last_edited_date": _now(),
    }
