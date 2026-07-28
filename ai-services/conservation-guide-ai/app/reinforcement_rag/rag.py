import os

from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings

from ..llm import llm
from .create_vector_db import COLLECTION_NAME, EMBEDDING_MODEL, PERSIST_DIR

# 벡터 DB는 모듈에서 한 번만 열어서 재사용 (요청마다 재오픈 방지)
_vector_db: Chroma | None = None


def _get_vector_db() -> Chroma:
    global _vector_db
    if _vector_db is None:
        embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL, api_key=os.environ["OPENAI_API_KEY"])
        _vector_db = Chroma(
            persist_directory=str(PERSIST_DIR),
            embedding_function=embeddings,
            collection_name=COLLECTION_NAME,
        )
    return _vector_db


# HyDE: 질문에 바로 유사도 검색을 걸지 않고, 먼저 "그럴듯한 답변 문서"를 LLM으로 생성한 뒤
# 그 가상 문서를 임베딩해서 검색에 사용한다. (질문 자체보다 답변 형태가 실제 문헌과 더 가깝기 때문)
def _generate_hypothetical_doc(query: str) -> str:
    prompt = f"""당신은 문화재 보존처리 전문가입니다.
    아래 질문에 대해, 보존처리 관련 문헌에 실려 있을 법한 전문적인 설명을 한 문단으로 작성해주세요.
    내용이 실제로 정확한지는 중요하지 않습니다. 벡터 검색에 사용할 것이므로 관련 전문 용어를 풍부하게 포함해서 작성해주세요.

    #질문
    {query}"""

    response = llm.invoke(prompt)

    print("==============================HyDE 검색용 문서 생성==============================")
    print(response.content)

    return response.content


# 강화제/용매 추천 등 여러 노드에서 공통으로 참고할 수 있는 검색 함수.
# HyDE로 생성한 가상 문서를 기준으로 reinforcement_docs 벡터 DB에서 유사한 청크를 검색해 반환.
def retrieve_reference_context(query: str, k: int = 4) -> list[dict]:
    hypothetical_doc = _generate_hypothetical_doc(query)

    vector_db = _get_vector_db()

    results = vector_db.similarity_search(hypothetical_doc, k=k)
    print("==============================HyDE 검색 결과==============================")
    print(results)

    return [
        {
            "content": doc.page_content,
            "source": doc.metadata.get("source"),
            "page": doc.metadata.get("page"),
        }
        for doc in results
    ]
