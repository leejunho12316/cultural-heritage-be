from dataclasses import dataclass
from typing import NewType


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
class AssessmentFinding:
    category: str
    severity: str
    message: str


@dataclass(frozen=True, slots=True)
class AssessmentReport:
    run: AssessmentRun
    summary: str
    findings: tuple[AssessmentFinding, ...]
