from typing import Literal
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


# 세척 - 상태/오염물 분석 및 세척법 필요여부
class CleaningAnalysis(BaseModel):
    relic_condition_summary: str = Field(description="유물 상태 요약")
    contamination_summary: str = Field(description="오염물 종류 요약")
    need_physical_cleaning: bool = Field(description="물리적 세척이 필요한지 여부")
    need_chemical_cleaning: bool = Field(description="화학적 세척이 필요한지 여부")
    reason: str = Field(description="위 판단의 근거")

# 세척 - 세척법 단계별 안내
class CleaningStep(BaseModel):
    order: int = Field(description="수행 순서 (1부터 시작)")
    method_type: str = Field(description="'physical' 또는 'chemical'")
    label: str = Field(description="이 단계에서 수행할 작업 설명")
    caution: str = Field(description="이 단계에서 특히 주의할 점 한 줄")

class CleaningGuide(BaseModel):
    steps: list[CleaningStep]
    overall_caution: str = Field(description="전체 세척 작업에서 가장 중요한 주의사항 한 줄")

# 세척 - 건조 안내
class DryingStep(BaseModel):
    order: int = Field(description="수행 순서 (1부터 시작)")
    label: str = Field(description="이 단계에서 수행할 작업 설명")
    caution: str = Field(description="이 단계에서 특히 주의할 점 한 줄")

class DryingGuide(BaseModel):
    steps: list[DryingStep]
    overall_caution: str = Field(description="건조 작업에서 가장 중요한 주의사항 한 줄")


# 강화처리 - 강화제/용매 추천
class ReinforcementAgentRecommendation(BaseModel):
    recommended_agent: str = Field(description="추천 강화제 이름")
    recommended_solvent: str = Field(description="추천 유기용매 이름")
    reason: str = Field(description="이 조합을 추천하는 이유")

# 강화처리 - 습윤 효과(색 변화) 분석 (VLM)
class ColorChangeAnalysis(BaseModel):
    severity: Literal["mild", "moderate", "severe"] = Field(description="색 변화 심각도")
    description: str = Field(description="관찰된 색 변화에 대한 설명")
    recommendation: str = Field(description="심각한 경우 개선 방향 (수지 변경/농도 낮추기/희석제 변경 등)")

# 강화처리 - 처리 방법 안내
class ReinforcementStep(BaseModel):
    order: int = Field(description="수행 순서 (1부터 시작)")
    label: str = Field(description="이 단계에서 수행할 작업 설명")
    caution: str = Field(description="이 단계에서 특히 주의할 점 한 줄")

class ReinforcementMethod(BaseModel):
    method_type: str = Field(description="'분무법' 또는 '침지법'")
    steps: list[ReinforcementStep]
    overall_caution: str = Field(description="전체 강화처리 작업에서 가장 중요한 주의사항 한 줄")


# 접합 - 접착제 추천
class BondingAdhesiveRecommendation(BaseModel):
    recommended_adhesive: str = Field(description="추천 접착제 이름")
    reason: str = Field(description="이 접착제를 추천하는 이유")
    precautions: list[str] = Field(description="사용 시 주의사항")

# 접합 - 접합 방식/단계 안내
class BondingStep(BaseModel):
    order: int = Field(description="수행 순서 (1부터 시작)")
    label: str = Field(description="이 단계에서 수행할 작업 설명")
    caution: str = Field(description="이 단계에서 특히 주의할 점 한 줄")

class BondingMethodRecommendation(BaseModel):
    method_type: str = Field(description="'복합접합', '단일접합', '결합접합', '모세관접합' 중 하나")
    steps: list[BondingStep]
    overall_caution: str = Field(description="전체 접합 작업에서 가장 중요한 주의사항 한 줄")


# 복원 - 복원 재료(합성수지) 추천
class RestorationMaterialRecommendation(BaseModel):
    recommended_material: str = Field(description="추천 복원 재료 이름")
    reason: str = Field(description="이 재료를 추천하는 이유")

# 복원 - 복원 방법 안내
class RestorationStep(BaseModel):
    order: int = Field(description="수행 순서 (1부터 시작)")
    label: str = Field(description="이 단계에서 수행할 작업 설명")
    caution: str = Field(description="이 단계에서 특히 주의할 점 한 줄")

class RestorationGuide(BaseModel):
    steps: list[RestorationStep]
    overall_caution: str = Field(description="전체 복원 작업에서 가장 중요한 주의사항 한 줄")