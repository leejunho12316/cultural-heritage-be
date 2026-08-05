from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok"]
    service: Literal["vca-ai"]
    mode: Literal["deterministic"]


class AssessmentRunCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    assessmentId: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    projectName: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    inputImageFolder: str = Field(min_length=1, max_length=4096)


class AssessmentRunResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    runId: str
    assessmentId: str
    status: Literal["COMPLETED"]


class AssessmentFindingResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    category: Literal["PLACEHOLDER"]
    severity: Literal["INFO"]
    message: Literal["VCA dry-run completed."]


class AssessmentReportResponse(AssessmentRunResponse):
    summary: Literal["Deterministic VCA assessment placeholder."]
    findings: tuple[AssessmentFindingResponse, ...]
