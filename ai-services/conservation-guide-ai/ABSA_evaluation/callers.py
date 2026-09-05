"""ABSA / Baseline VLM 호출 함수 모음.

_to_image_content는 app/nodes/reinforcement.py에서 verbatim 복사.
reinforcement.py를 직접 import하면 reinforcement_rag.rag가 연쇄 로드되어
Chroma DB 초기화가 트리거되므로, 7줄짜리 순수 함수를 여기에 복사한다.
"""
import base64
import mimetypes

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

# config.py의 sys.path 설정으로 app/schemas.py가 임포트 가능해짐
from .config import EVAL_VISION_MODEL, EVAL_TEMPERATURE
from .models import BaselineResult
from .prompts import ABSA_PROMPT_TEMPLATE, BASELINE_PROMPT_TEMPLATE
from schemas import ColorChangeAnalysis  # noqa: E402 (app/ 경로, config.py가 추가)


# Source: app/nodes/reinforcement.py — _to_image_content()
def _to_image_content(path_or_url: str) -> dict:
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return {"type": "image_url", "image_url": {"url": path_or_url}}

    mime_type, _ = mimetypes.guess_type(path_or_url)
    mime_type = mime_type or "image/png"
    with open(path_or_url, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    return {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}}


def call_absa(
    before_urls: list[str],
    after_urls: list[str],
    relic_info: dict,
    confirmed_agent: dict,
    api_key: str,
    model: str = EVAL_VISION_MODEL,
    temperature: float = EVAL_TEMPERATURE,
) -> ColorChangeAnalysis:
    """9개 aspect 구조화 프롬프트로 VLM 호출 (ABSA 방식)."""
    llm = ChatOpenAI(model=model, temperature=temperature, api_key=api_key)
    structured_vlm = llm.with_structured_output(ColorChangeAnalysis)

    content = [{
        "type": "text",
        "text": ABSA_PROMPT_TEMPLATE.format(relic_info=relic_info, confirmed_agent=confirmed_agent),
    }]
    for url in before_urls:
        content.append({"type": "text", "text": "[테스트 전 사진]"})
        content.append(_to_image_content(url))
    for url in after_urls:
        content.append({"type": "text", "text": "[테스트 후 사진]"})
        content.append(_to_image_content(url))

    return structured_vlm.invoke([HumanMessage(content=content)])


def call_baseline(
    before_urls: list[str],
    after_urls: list[str],
    relic_info: dict,
    confirmed_agent: dict,
    api_key: str,
    model: str = EVAL_VISION_MODEL,
    temperature: float = EVAL_TEMPERATURE,
) -> BaselineResult:
    """9개 aspect 구조 없이 holistic하게 VLM 호출 (Baseline)."""
    llm = ChatOpenAI(model=model, temperature=temperature, api_key=api_key)
    structured_vlm = llm.with_structured_output(BaselineResult)

    content = [{
        "type": "text",
        "text": BASELINE_PROMPT_TEMPLATE.format(relic_info=relic_info, confirmed_agent=confirmed_agent),
    }]
    for url in before_urls:
        content.append({"type": "text", "text": "[테스트 전 사진]"})
        content.append(_to_image_content(url))
    for url in after_urls:
        content.append({"type": "text", "text": "[테스트 후 사진]"})
        content.append(_to_image_content(url))

    return structured_vlm.invoke([HumanMessage(content=content)])
