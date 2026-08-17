from dataclasses import dataclass
from typing import Final, NewType

from app.services.vca_rag_artifacts import VcaRagArtifacts


# modules.orchestration.startup이 이 순서대로 output/<stage>/<project_name>
# 디렉터리를 만드는 파이프라인 스테이지 이름들. assessment_runs(재실행 전
# 산출물 삭제)와 vca_resume(어디까지 재사용 가능한지 판단) 양쪽에서 쓰는
# 공유 상수라서, 둘 중 어느 쪽에도 속하지 않는 이 모델 계층에 둔다.
ENGINE_OUTPUT_STAGES: Final = (
    "preprocessing",
    "rough_masking",
    "visual_cue_generation",
    "rag",
    "prompt_generating",
    "mask_refining",
    "anomaly_grouping",
    "report_generating",
    "result",
)


AssessmentId = NewType("AssessmentId", str)
AssessmentRunId = NewType("AssessmentRunId", str)
InputImageFolder = NewType("InputImageFolder", str)
MaxImages = NewType("MaxImages", str)
ProjectName = NewType("ProjectName", str)
RunTimeoutSeconds = NewType("RunTimeoutSeconds", int)


@dataclass(frozen=True, slots=True)
class AssessmentRun:
    run_id: AssessmentRunId
    assessment_id: AssessmentId
    project_name: ProjectName


@dataclass(frozen=True, slots=True)
class AssessmentFindingCitation:
    citation_id: str
    source_citation: str | None
    page_number: int | None


@dataclass(frozen=True, slots=True)
class AssessmentFindingBbox:
    x_min: float
    y_min: float
    x_max: float
    y_max: float


@dataclass(frozen=True, slots=True)
class AssessmentFinding:
    category: str
    severity: str
    message: str
    candidate_id: str | None = None
    image_id: str | None = None
    concept_family: str | None = None
    descriptor: str | None = None
    citations: tuple[AssessmentFindingCitation, ...] = ()
    bbox: AssessmentFindingBbox | None = None
    polygons: tuple[tuple[tuple[float, float], ...], ...] | None = None


@dataclass(frozen=True, slots=True)
class AssessmentReport:
    run: AssessmentRun
    summary: str
    findings: tuple[AssessmentFinding, ...]
    rag: VcaRagArtifacts | None = None


@dataclass(frozen=True, slots=True)
class AssessmentStage:
    name: str
    status: str
    exit_code: int | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class AssessmentStageProgress:
    completed: int
    total: int


@dataclass(frozen=True, slots=True)
class AssessmentProgress:
    status: str
    current_stage: str | None
    stages: tuple[AssessmentStage, ...]
    failure_reason: str | None = None
    current_stage_progress: AssessmentStageProgress | None = None
