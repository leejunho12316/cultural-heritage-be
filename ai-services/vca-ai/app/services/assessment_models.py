from dataclasses import dataclass
from typing import NewType

from app.services.vca_rag_artifacts import VcaRagArtifacts


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
class AssessmentProgress:
    status: str
    current_stage: str | None
    stages: tuple[AssessmentStage, ...]
    failure_reason: str | None = None
