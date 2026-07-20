from pydantic import BaseModel, Field

#ID 규칙 : 큰 단계 (해체, 세척 등) - 작은 단계 (체크리스트, 도구 추천 등) - 고유 id (01,02,03)
# id는 LLM이 만들지 않고, state.py의 assign_ids()가 결과 리스트에 후처리로 붙인다.

# 해체 - 체크리스트
class ChecklistItem(BaseModel):
    label: str = Field(description="확인할 내용 설명")
    recommended: bool = Field(description="AI가 권장하는 기본 체크 상태")

class DisassemblyChecklist(BaseModel):
    checklist: list[ChecklistItem]
    caution: str = Field(description="가장 중요한 주의사항 한 줄")

# 해체 - 도구 추천
class RecommendedTool(BaseModel):
    name: str = Field(description="추천 도구 이름")
    description: str = Field(description="이 도구를 추천하는 이유/설명")

class ToolRecommendation(BaseModel):
    recommended_tools: list[RecommendedTool] = Field(description="추천 도구 목록")
    reason: str = Field(description="이 도구들을 추천하는 이유 한 줄")
    precautions: list[str] = Field(description="도구 사용 시 주의사항")

# 해체 - 단계별 절차
class DisassemblyStep(BaseModel):
    order: int = Field(description="수행 순서 (1부터 시작)")
    label: str = Field(description="이 단계에서 수행할 작업 설명")
    tools_used: list[str] = Field(description="이 단계에서 사용하는 도구")
    caution: str = Field(description="이 단계에서 특히 주의할 점 한 줄")

class DisassemblyMethod(BaseModel):
    steps: list[DisassemblyStep]
    overall_caution: str = Field(description="전체 해체 작업에서 가장 중요한 주의사항 한 줄")