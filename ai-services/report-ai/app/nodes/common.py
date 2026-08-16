"""섹션 노드들이 공유하는 LLM 호출 헬퍼."""

from ..llm import llm
from ..rag import retrieve_reference_context
from ..schemas import ReportSection


def build_section_via_llm(
    *, system_prompt: str, data_context: str, rag_query: str, top_k: int = 3
) -> ReportSection:
    """참고 문헌(문체 참고용)과 이번 유물의 실제 데이터를 함께 넣어 섹션을 생성한다."""
    try:
        reference_chunks = retrieve_reference_context(
            rag_query,
            filters={"material": ["general"], "process_stage": "report"},
            top_k=top_k,
        )
    except (ValueError, FileNotFoundError):
        # 인덱스가 아직 없는 로컬 개발 환경 등에서도 생성 자체는 막지 않는다.
        reference_chunks = []

    reference_text = (
        "\n\n".join(
            f"[{chunk['source']} p.{chunk['page']}] {chunk['content']}"
            for chunk in reference_chunks
        )
        or "(참고 문헌 없음)"
    )

    prompt = f"""{system_prompt}

# 참고 문헌 (문체·구성 참고용 — 내용을 그대로 베끼지 말고 서술 방식만 참고하세요)
{reference_text}

# 이번 유물의 실제 데이터
{data_context}
"""
    structured = llm.with_structured_output(ReportSection)
    return structured.invoke(prompt)
