from pydantic import BaseModel, Field


class ReportSection(BaseModel):
    """보고서 섹션 하나. 노드가 LLM으로 채우는 표준 산출 형식."""

    title: str = Field(description="보고서에 표시될 섹션 제목")
    body: str = Field(description="정식 보고서체로 작성된 완성 문장")
