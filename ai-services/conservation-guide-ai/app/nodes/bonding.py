import base64
import mimetypes

from langchain_core.messages import HumanMessage
from langgraph.types import interrupt

from ..state import State, _now, stage_guard, _build_result, assign_ids
from ..schemas import BondingAdhesiveRecommendation, BondingMethodRecommendation, BondingTempAnalysis
from ..llm import llm, vision_llm
from ..bonding_rag.rag import retrieve_reference_context

# 접합 노드!!

# http(s) URL은 그대로, 로컬 파일 경로는 base64 data URI로 변환해서 vision 모델에 전달.
def _to_image_content(path_or_url: str) -> dict:
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return {"type": "image_url", "image_url": {"url": path_or_url}}

    mime_type, _ = mimetypes.guess_type(path_or_url)
    mime_type = mime_type or "image/png"
    with open(path_or_url, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    return {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}}

# relic_info의 자유 텍스트 재질값을 bonding_rag의 표준 material 값으로 변환.
# 매칭되는 키워드가 없으면 "general"만 남아 공통 문헌만 검색된다.
_MATERIAL_KEYWORDS = {
    "ceramic": ("토기", "도자기", "자기", "청자", "백자", "옹기", "ceramic", "pottery"),
    "metal": ("금속", "청동", "철기", "금동", "metal", "bronze"),
    "wood": ("목재", "목기", "wood"),
    "stone": ("석조", "석재", "stone"),
    "paper": ("지류", "종이", "paper"),
    "textile": ("직물", "섬유", "textile"),
}


def _resolve_materials(relic_info: dict) -> list[str]:
    raw_material = str(relic_info.get("material", ""))
    matched = [
        material
        for material, keywords in _MATERIAL_KEYWORDS.items()
        if any(keyword in raw_material for keyword in keywords)
    ]
    # "general"은 항상 포함해 공통(재질 무관) 접합 지침도 함께 검색한다.
    return matched + ["general"]


# 접착제 추천
def _get_adhesive_recommendation(relic_info: dict, reinforcement_agent: dict) -> dict:

    structured_llm = llm.with_structured_output(BondingAdhesiveRecommendation)

    # top_k를 크게 두지 않는다 — 청크를 많이 넣을수록 프롬프트가 길어져 응답이 느려지고,
    # 관련도 낮은 청크가 섞여 판단 정확도가 오히려 떨어질 수 있다.
    reference_chunks = retrieve_reference_context(
        f"{relic_info} 유물의 접합용 접착제 선택 — 재질 적합성, 가역성, 접착강도, 경화시간",
        filters={
            "material": _resolve_materials(relic_info),
            "process_stage": "bonding",
        },
        top_k=3,
    )
    reference_text = "\n\n".join(
        f"[출처: {chunk['source']} p.{chunk['page']}]\n{chunk['content']}"
        for chunk in reference_chunks
    ) or "(관련 문헌 없음)"

    prompt = f"""역할: 문화재 보존처리 전문가.
과제: 아래 유물 정보로 접합 접착제 1개를 선택하고 이유·주의사항을 제시.

후보(이 중에서만 선택): Paraloid B-72 / Cemedine C / Araldite rapid / Cyanoacrylate / poly urethane / Loctite 401

판정 규칙(위에서 아래로 순서대로 적용, 앞 규칙이 뒤 규칙보다 우선):
1. 재질 — 연질토기면 저점도 순간접착제(Cyanoacrylate) 제외.
2. 처리목적 — 전시용은 가역성 우선, 수장/연구용은 강도 우선.
3. 무게·접합면 — 무겁거나 접합면이 좁으면 경화 전 클램핑이 안정적인 접착제 우선, 초기 접착력 약한 것 제외.
4. 정렬 소요시간 — 파편 여러 개로 정렬에 시간 걸리면 순간경화형 제외. 반대로 즉시 고정이 필요하면 지나치게 느린 경화형 제외.
5. 상성 — 강화처리 단계 강화제와 화학적으로 상충하지 않는 것 우선.

근거 문헌(위 규칙과 상충하면 규칙을 우선):
{reference_text}

유물 정보: {relic_info}
확정된 강화제/용매: {reinforcement_agent}"""

    result: BondingAdhesiveRecommendation = structured_llm.invoke(prompt)

    return result.model_dump()


# 접합 방식(복합/단일/결합/모세관접합) + 단계별 안내
def _get_bonding_method(relic_info: dict, confirmed_adhesive: dict) -> dict:

    structured_llm = llm.with_structured_output(BondingMethodRecommendation)

    prompt = f"""역할: 문화재 보존처리 전문가.
과제: 복합접합/단일접합/결합접합/모세관접합 중 하나를 선택하고, 확정된 접착제로 접합을 완료하는 작업 단계를 안내.

단계를 나누는 기준(중요 — 이 기준을 지켜야 실무자가 바로 쓸 수 있습니다):
- 1단계 = 작업자가 별도로 판단하거나 실수하면 손상으로 이어지는 지점 1개.
- 순서만 이어지고 판단이 필요 없는 동작들(예: 붓으로 이물질 제거 → 마른 천으로 닦기)은 하나의 단계로 합치세요.
- "보호구 착용" 같은 일반 안전수칙은 넣지 말고, 이 접착제·이 유물 특유의 위험(가사시간, 경화 중 이동 금지, 과다도포로 인한 변색 등)만 caution에 담으세요.
- 정확히 5단계가 되도록 작성하세요. 위 기준을 적용했을 때 5개보다 적으면 판단이 필요한
  하위 지점을 더 세분화하고, 5개보다 많으면 인접한 저위험 동작끼리 합쳐서 정확히 5개를
  채우세요.

label, caution, overall_caution은 자연스러운 한국어 문장으로 풀어서 작성하고,
·, /, (), {{}} 같은 기호는 최대한 쓰지 마세요.
예를 들어 "아세톤/에탄올" 대신 "아세톤이나 에탄올", "(농도 20%)" 대신 "농도는 20퍼센트로" 처럼 표현하세요.

유물 정보: {relic_info}
확정된 접착제: {confirmed_adhesive}"""

    result: BondingMethodRecommendation = structured_llm.invoke(prompt)

    data = result.model_dump()
    data["steps"] = assign_ids(data["steps"], "bonding", "method")

    return data


# 임시접합(가조립) 사진 검증 (VLM).
# 평가 기준은 3D 파편 정합 연구의 판단 기준을 2D 사진 정성 평가로 옮긴 것:
# - 축 정렬(axis_alignment): PotSAC의 회전축 추정 개념 — 비틀림/기울어짐 없이 맞춰졌는지
# - 파단면 매칭(fracture_match_quality): Structure-from-Sherds의 오정합(false-positive match) 방지 개념
#   — 간극·단차 없이, 억지로 끼워 맞춘 흔적 없이 파단면끼리 맞물렸는지
def _get_temp_bonding_analysis(
    relic_info: dict,
    confirmed_adhesive: dict,
    before_photo_urls: list,
    after_photo_urls: list,
) -> dict:

    structured_vlm = vision_llm.with_structured_output(BondingTempAnalysis)

    content = [{
        "type": "text",
        "text": f"""당신은 문화재 보존처리 전문가입니다.
        임시접합(가조립) 전/후 사진을 비교해서 접합 상태를 평가해주세요.

        가장 먼저 확인할 것: 사진이 실제로 도자기 파편/접합부를 판단할 수 있는 사진인지 확인하세요.
        접합과 무관한 사진, 유물이 안 보이는 사진, 너무 흐리거나 멀어서 파단면이 안 보이는 사진이면
        is_analyzable=false로 표시하고, description에 이유를, recommendation에 어떤 사진(각도/거리/초점)을
        다시 찍어서 보내야 하는지 구체적으로 요청하세요. axis_alignment/fracture_match_quality는 'unclear'로 두세요.

        분석 가능한 사진이면 다음 두 기준으로 평가하세요.
        1. 축 정렬(axis_alignment): 기물이 원래 형태의 회전축을 기준으로 비틀리거나 기울어지지 않고 정렬되었는지.
        2. 파단면 매칭(fracture_match_quality): 파편의 파단면(깨진 단면)끼리 간극이나 단차 없이 맞물렸는지, 억지로 끼워 맞춘 흔적(오정합)은 없는지.

        문제가 있다면 어느 부분을 어떻게 다시 맞춰야 하는지 구체적으로 제안해주세요.

        참고로 이 유물과 이번에 확정된 접착제 정보는 다음과 같습니다(재질별 파편 두께/무게
        차이로 인한 정렬 난이도, 접착제의 가사시간에 따른 재조정 여지 등을 판단할 때 참고하세요).
        유물 정보: {relic_info}
        확정된 접착제: {confirmed_adhesive}"""
    }]
    for url in before_photo_urls:
        content.append({"type": "text", "text": "[임시접합 전 사진]"})
        content.append(_to_image_content(url))
    for url in after_photo_urls:
        content.append({"type": "text", "text": "[임시접합 후 사진]"})
        content.append(_to_image_content(url))

    result: BondingTempAnalysis = structured_vlm.invoke([HumanMessage(content=content)])

    return result.model_dump()




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


# (3-2) 임시접합 전/후 사진 입력 : interrupt() 만 담당. (검증은 다음 노드에서 VLM으로 수행)
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


# (3-2) 임시접합 사진 검증 : VLM 호출 1회.
@stage_guard("bonding")
def bonding_temp_analysis_node(state: State):
  relic_info = state.get("relic_info", {})
  confirmed_adhesive = state["results"]["bonding"]["confirmed_adhesive"]
  temp_bonding = state["results"]["bonding"]["temp_bonding"]

  ai_temp_analysis = _get_temp_bonding_analysis(
      relic_info,
      confirmed_adhesive,
      temp_bonding.get("before_photo_urls", []),
      temp_bonding.get("after_photo_urls", []),
  )

  return {
    "cur_flow": "bonding",
    "results": {
        "bonding": {"ai_temp_analysis": ai_temp_analysis}},
    "last_edited_date": _now(),
  }


# (3-2) 임시접합 검증 결과 확인 : interrupt() 만 담당.
# FE input 형식 : {"action": "proceed" 또는 "retry"}
@stage_guard("bonding")
def bonding_confirm_temp_analysis_node(state: State):
  ai_temp_analysis = state["results"]["bonding"]["ai_temp_analysis"]

  confirmed_temp_analysis = interrupt({
    "stage": "접합 - 임시접합 검증 결과를 확인하고 진행 여부를 선택하세요!",
    "ai_temp_analysis": ai_temp_analysis,
    "default_action": "proceed",
  })

  return {
    "cur_flow": "bonding",
    "results": {
        "bonding": {"confirmed_temp_analysis": confirmed_temp_analysis}},
    "last_edited_date": _now(),
  }


# 임시접합 검증 결과에 따른 분기 : "retry"면 (3-2) 임시접합 사진 재입력으로, "proceed"면 (3-3) 접합 방법으로.
# bonding 단계 자체가 flow에 없어 stage_guard에 의해 스킵된 경우 confirmed_temp_analysis가
# 아예 존재하지 않을 수 있으므로, 그 경우는 그냥 다음 단계로 진행시킨다.
def route_after_temp_analysis(state: State) -> str:
  confirmed_temp_analysis = state.get("results", {}).get("bonding", {}).get("confirmed_temp_analysis")
  if not confirmed_temp_analysis:
      return "proceed"
  return "retry" if confirmed_temp_analysis.get("action") == "retry" else "proceed"


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
