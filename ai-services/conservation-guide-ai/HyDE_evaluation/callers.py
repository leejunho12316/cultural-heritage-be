"""Plain RAG / HyDE RAG 검색 및 강화제·용매 추천 호출 함수 모음."""
from __future__ import annotations

import os

from langchain_chroma import Chroma
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from .config import (
    VECTOR_STORE_DIR,
    COLLECTION_NAME,
    EVAL_EMBED_MODEL,
    EVAL_LLM_MODEL,
    DEFAULT_RETRIEVAL_K,
)

# app/schemas.py 는 config.py 의 sys.path 처리 덕분에 임포트 가능
from schemas import ReinforcementAgentRecommendation  # type: ignore[import]

_vector_db: Chroma | None = None


def _get_vector_db(api_key: str) -> Chroma:
    global _vector_db
    if _vector_db is None:
        embeddings = OpenAIEmbeddings(model=EVAL_EMBED_MODEL, api_key=api_key)
        _vector_db = Chroma(
            persist_directory=str(VECTOR_STORE_DIR),
            embedding_function=embeddings,
            collection_name=COLLECTION_NAME,
        )
    return _vector_db


def _docs_to_list(docs) -> list[dict]:
    return [
        {
            "content": doc.page_content,
            "source": doc.metadata.get("source"),
            "page": doc.metadata.get("page"),
        }
        for doc in docs
    ]


def _generate_hypothetical_doc(query: str, api_key: str, model: str) -> str:
    llm = ChatOpenAI(model=model, temperature=0, api_key=api_key)
    prompt = f"""당신은 한국 문화재 보존처리 지침서를 집필하는 보존과학 전문가입니다.
아래 유물 정보를 바탕으로, 실제 보존처리 지침서·학술 보고서에 실릴 법한 강화처리 단락을 작성하세요.

[작성 규칙]
1. 해당 재질과 손상 상태에 적합한 강화제와 용매를 반드시 명시할 것
   - 강화제 후보: Paraloid B72 / HPC / 폴리비닐부티랄 / 수용성 Emulsion / Paraloid NAD-10
   - 용매 후보: 아세톤 / 에탄올 / 톨루엔 / 자일렌 / 물 / 나프타 / 화이트스피릿 등
2. 강화제 선택 근거(재질 특성과의 연관), 희석 농도, 도포 방법을 포함할 것
3. 문헌 특유의 서술체 사용 ("~를 권장한다", "~% 용액을 도포한다", "~에 용해하여 사용한다")
4. 180자 내외로 작성

[유물 정보]
{query}"""
    response = llm.invoke(prompt)
    return response.content




def retrieve_plain_rag(
    query: str,
    api_key: str,
    k: int = DEFAULT_RETRIEVAL_K,
) -> list[dict]:
    """질문을 그대로 Chroma embedding 검색."""
    db = _get_vector_db(api_key)
    docs = db.similarity_search(query, k=k)
    return _docs_to_list(docs)


def retrieve_hyde_rag(
    query: str,
    api_key: str,
    model: str = EVAL_LLM_MODEL,
    k: int = DEFAULT_RETRIEVAL_K,
) -> tuple[str, list[dict]]:
    """HyDE: 가상 문서 생성 후 Chroma 검색. (가상 문서, 청크 리스트) 반환."""
    hypo_doc = _generate_hypothetical_doc(query, api_key, model)
    db = _get_vector_db(api_key)
    docs = db.similarity_search(hypo_doc, k=k)
    return hypo_doc, _docs_to_list(docs)


def call_recommendation(
    chunks: list[dict],
    relic_info: dict,
    api_key: str,
    model: str = EVAL_LLM_MODEL,
    temperature: float = 0.0,
) -> ReinforcementAgentRecommendation:
    """검색 청크를 넣어 강화제·용매 추천 생성 (reinforcement.py 프롬프트 동일)."""
    llm = ChatOpenAI(model=model, temperature=temperature, api_key=api_key)
    structured = llm.with_structured_output(ReinforcementAgentRecommendation)

    reference_text = "\n\n".join(
        f"[출처: {chunk['source']} p.{chunk['page']}]\n{chunk['content']}"
        for chunk in chunks
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

아래는 보존처리 참고 문헌에서 검색된 관련 내용입니다. 위 규칙과 상충하지 않는 범위에서 참고하세요.

#참고 문헌
{reference_text}

#정보
유물 정보: {relic_info}"""

    return structured.invoke(prompt)
