from typing import Literal
from pydantic import BaseModel, Field

#ID 규칙 : 큰 단계 (해체, 세척 등) - 작은 단계 (체크리스트, 도구 추천 등) - 고유 id (01,02,03)
# id는 LLM이 만들지 않고, state.py의 assign_ids()가 결과 리스트에 후처리로 붙인다.

# 해체 - 체크리스트
class ChecklistItem(BaseModel):
    label: str = Field(description="확인할 내용 설명")
    recommended: bool = Field(description="AI가 권장하는 기본 체크 상태")

class DisassemblyChecklist(BaseModel):
    checklist: list[ChecklistItem] = Field(min_length=10, max_length=10, description="해체 전 확인할 체크리스트 항목 10개")
    caution: str = Field(description="가장 중요한 주의사항 한 줄")

# 해체 - 도구 추천
class RecommendedTool(BaseModel):
    name: str = Field(description="도구 이름")
    description: str = Field(description="이 도구가 필요한 이유/설명")
    recommended: bool = Field(description="지금 상황에 반드시 필요한 필수 도구인지 여부(기본 체크 상태)")

class ToolRecommendation(BaseModel):
    recommended_tools: list[RecommendedTool] = Field(min_length=3, max_length=3, description="추천 도구 목록 3개 (이 중 반드시 1개만 recommended: true)")
    reason: str = Field(description="이 도구들을 추천하는 이유 한 줄")
    precautions: list[str] = Field(description="도구 사용 시 주의사항")

# 해체 - 단계별 절차
class DisassemblyStep(BaseModel):
    order: int = Field(description="수행 순서 (1부터 시작)")
    label: str = Field(description="이 단계에서 수행할 작업 설명")
    tools_used: list[str] = Field(description="이 단계에서 사용하는 도구")
    caution: str = Field(description="이 단계에서 특히 주의할 점 한 줄")

class DisassemblyMethod(BaseModel):
    steps: list[DisassemblyStep] = Field(min_length=5, max_length=5, description="해체 작업을 정확히 5단계로 요약한 순서")
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
    steps: list[CleaningStep] = Field(min_length=5, max_length=5, description="세척 작업을 정확히 5단계로 요약한 순서")
    overall_caution: str = Field(description="전체 세척 작업에서 가장 중요한 주의사항 한 줄")

# 세척 - 건조 안내
class DryingStep(BaseModel):
    order: int = Field(description="수행 순서 (1부터 시작)")
    label: str = Field(description="이 단계에서 수행할 작업 설명")
    caution: str = Field(description="이 단계에서 특히 주의할 점 한 줄")

class DryingGuide(BaseModel):
    steps: list[DryingStep] = Field(min_length=5, max_length=5, description="건조 작업을 정확히 5단계로 요약한 순서")
    overall_caution: str = Field(description="건조 작업에서 가장 중요한 주의사항 한 줄")


# 강화처리 - 강화제/용매 추천
class ReinforcementAgentRecommendation(BaseModel):
    recommended_agent: Literal["Paraloid B72", "HPC", "폴리비닐부티랄", "수용성 Emulsion", "Paraloid NAD-10"] = Field(description="추천 강화제")
    recommended_solvent: Literal["아세톤", "톨루엔", "자일렌", "에틸아세테이트", "이소프로판올", "에탄올", "MEK", "아밀아세테이트", "메탄올", "물", "나프타", "화이트스피릿"] = Field(description="추천 유기용매")
    reason: str = Field(description="이 조합을 추천하는 이유")

# 강화처리 - 습윤 효과 테스트 개별 항목 결과 (ABSA 스타일 분석의 각 aspect)
class AspectResult(BaseModel):
    severity: Literal["none", "mild", "moderate", "severe"] = Field(description="이 항목의 변화 심각도 (none: 변화 없음)")
    description: str = Field(description="이 항목에서 전/후 사진을 비교해 관찰한 내용 설명")

# 강화처리 - 습윤 효과(색상/광택/표면) 종합 분석 (VLM, ABSA 스타일 9개 항목 + 종합판정)
class ColorChangeAnalysis(BaseModel):
    hue_shift: AspectResult = Field(description="색상(색조) 변화 - 색 자체가 다른 색으로 옮겨갔는지")
    brightness_change: AspectResult = Field(description="명도 변화 - 전체적으로 어두워지거나 밝아졌는지")
    saturation_change: AspectResult = Field(description="채도 변화 - 색이 더 선명해지거나 탁해졌는지")
    gloss_change: AspectResult = Field(description="광택 변화 - 무광이던 표면이 강화제 수지막으로 인해 유광으로 바뀌는 등의 변화")
    blanching: AspectResult = Field(description="백화현상 - 용제가 증발하면서 표면이 하얗게 뜨는 현상")
    uneven_penetration: AspectResult = Field(description="얼룩/불균일 침투 - 강화제가 고르게 스며들지 않아 생기는 얼룩이나 경계 자국(tide-line)")
    edge_visibility: AspectResult = Field(description="처리 경계 뚜렷함 - 처리 부위와 미처리 부위의 경계선이 도드라져 보이는지 (자연스럽게 섞여야 함)")
    crack_response: AspectResult = Field(description="균열부 반응 - 균열/틈에 강화제가 고이거나 그 부분만 진해지거나 하얘지는지")
    texture_change: AspectResult = Field(description="질감 변화 - 표면의 거칠기/매끄러움 등 촉감상 변화")
    overall_severity: Literal["mild", "moderate", "severe"] = Field(description="위 9개 항목을 종합한 전체 심각도")
    recommendation: str = Field(description="심각한 경우 개선 방향 (강화제 수지 변경/농도 낮추기/희석제 변경 등)")

# 강화처리 - 처리 방법 안내
class ReinforcementStep(BaseModel):
    order: int = Field(description="수행 순서 (1부터 시작)")
    label: str = Field(description="이 단계에서 수행할 작업 설명")
    caution: str = Field(description="이 단계에서 특히 주의할 점 한 줄")

class ReinforcementMethod(BaseModel):
    method_type: str = Field(description="'분무법' 또는 '침지법'")
    steps: list[ReinforcementStep] = Field(min_length=5, max_length=5, description="강화 처리 작업을 정확히 5단계로 요약한 순서")
    overall_caution: str = Field(description="전체 강화처리 작업에서 가장 중요한 주의사항 한 줄")


# 접합 - 접착제 추천
class BondingAdhesiveRecommendation(BaseModel):
    recommended_adhesive: Literal["Paraloid B-72", "Cemedine C", "Araldite rapid", "Cyanoacrylate", "poly urethane", "Loctite 401"] = Field(description="추천 접착제")
    reason: str = Field(description="이 접착제를 추천하는 이유")
    precautions: list[str] = Field(description="사용 시 주의사항")

# 접합 - 임시접합(가조립) 사진 검증 (VLM)
# 평가축은 3D 파편 정합 연구(PotSAC의 축 정렬 추정, Structure-from-Sherds의 파단면
# 오정합 방지)에서 쓰는 판단 기준을 2D 사진으로 정성 평가하는 형태로 옮긴 것.
class BondingTempAnalysis(BaseModel):
    is_analyzable: bool = Field(
        description="사진이 도자기 파편/접합부를 판단하기에 충분한지. 접합과 무관한 사진, 파단면이 안 보일 정도로 흐리거나 "
                    "너무 멀리서 찍힌 사진, 유물이 아예 안 보이는 사진 등은 False로 표시."
    )
    axis_alignment: Literal["good", "minor_issue", "major_issue", "unclear"] = Field(
        description="파편들이 원래 기물의 회전축을 기준으로 비틀림/기울어짐 없이 정렬되었는지. is_analyzable=False면 'unclear'."
    )
    fracture_match_quality: Literal["good", "minor_issue", "major_issue", "unclear"] = Field(
        description="파단면끼리 간극·단차 없이 맞물렸는지, 억지로 끼워 맞춘 흔적(오정합)은 없는지. is_analyzable=False면 'unclear'."
    )
    description: str = Field(description="관찰된 정렬/파단면 상태에 대한 설명. is_analyzable=False면 판단 불가 사유를 설명.")
    overall_severity: Literal["mild", "moderate", "severe"] = Field(
        description="종합 문제 심각도. is_analyzable=False인 경우에도 재촬영 전까지 진행 보류가 필요하므로 'severe'로 표시."
    )
    recommendation: str = Field(
        description="그대로 진행 가능한지, 재작업이 필요하면 어느 부분을 다시 맞춰야 하는지. "
                    "is_analyzable=False면 어떤 사진(각도/거리/초점)을 다시 촬영해서 보내야 하는지 구체적으로 요청."
    )

# 접합 - 접합 방식/단계 안내
class BondingStep(BaseModel):
    order: int = Field(description="수행 순서 (1부터 시작)")
    label: str = Field(description="이 단계에서 수행할 작업 설명")
    caution: str = Field(description="이 단계에서 특히 주의할 점 한 줄")

class BondingMethodRecommendation(BaseModel):
    method_type: str = Field(description="'복합접합', '단일접합', '결합접합', '모세관접합' 중 하나")
    steps: list[BondingStep] = Field(min_length=5, max_length=5, description="접합 작업을 정확히 5단계로 요약한 순서")
    overall_caution: str = Field(description="전체 접합 작업에서 가장 중요한 주의사항 한 줄")


# 복원 - 복원 재료(합성수지) 추천
class RestorationMaterialRecommendation(BaseModel):
    recommended_material: Literal["CDK-520", "Araldite SV427+HV427", "Epo-tec 301", "XTR-311", "Repairit Quik"] = Field(description="추천 복원 재료")
    reason: str = Field(description="이 재료를 추천하는 이유")

# 복원 - 복원 방법 안내
class RestorationStep(BaseModel):
    order: int = Field(description="수행 순서 (1부터 시작)")
    label: str = Field(description="이 단계에서 수행할 작업 설명")
    tools_used: list[str] = Field(description="이 단계에서 사용하는 도구/재료")
    caution: str = Field(description="이 단계에서 특히 주의할 점 한 줄")

class RestorationGuide(BaseModel):
    steps: list[RestorationStep] = Field(min_length=5, max_length=5, description="복원 작업을 정확히 5단계로 요약한 순서")
    overall_caution: str = Field(description="전체 복원 작업에서 가장 중요한 주의사항 한 줄")

# 복원 - 마감처리(연마·채색·광택) 안내
class RestorationFinishingStep(BaseModel):
    order: int = Field(description="수행 순서 (1부터 시작)")
    label: str = Field(description="이 단계에서 수행할 작업 설명")
    tools_used: list[str] = Field(description="이 단계에서 사용하는 도구/재료")
    caution: str = Field(description="이 단계에서 특히 주의할 점 한 줄")

class RestorationFinishingGuide(BaseModel):
    steps: list[RestorationFinishingStep] = Field(min_length=5, max_length=5, description="마감처리 작업을 정확히 5단계로 요약한 순서")
    overall_caution: str = Field(description="전체 마감처리 작업에서 가장 중요한 주의사항 한 줄")