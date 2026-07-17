from pydantic import BaseModel, Field



# 해체 - 체크리스트
class ChecklistItem(BaseModel):
    id: str = Field(description="체크리스트 항목의 짧은 영문 id")
    label: str = Field(description="확인할 내용 설명")
    recommended: bool = Field(description="AI가 권장하는 기본 체크 상태")

class DisassemblyChecklist(BaseModel):
    checklist: list[ChecklistItem]
    caution: str = Field(description="가장 중요한 주의사항 한 줄")

# 해체 - 도구 추천
class ToolRecommendation(BaseModel):
    recommended_tools: list[str] = Field(description="추천 도구 목록")
    reason: str = Field(description="이 도구들을 추천하는 이유 한 줄")
    precautions: list[str] = Field(description="도구 사용 시 주의사항")

# 해체 - 단계별 절차
class DisassemblyStep(BaseModel):
    id: str = Field(description="해체 단계의 짧은 영문 id")
    order: int = Field(description="수행 순서 (1부터 시작)")
    label: str = Field(description="이 단계에서 수행할 작업 설명")
    tools_used: list[str] = Field(description="이 단계에서 사용하는 도구")
    caution: str = Field(description="이 단계에서 특히 주의할 점 한 줄")

class DisassemblyMethod(BaseModel):
    steps: list[DisassemblyStep]
    overall_caution: str = Field(description="전체 해체 작업에서 가장 중요한 주의사항 한 줄")