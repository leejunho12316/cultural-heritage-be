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


class AssessmentStageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    status: str
    exitCode: int | None = None
    reason: str | None = None


class AssessmentRunResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    runId: str
    assessmentId: str
    status: Literal["RUNNING", "COMPLETED", "FAILED"]
    currentStage: str | None = None
    stages: tuple[AssessmentStageResponse, ...] = ()
    failureReason: str | None = None


class AssessmentFindingCitationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    citationId: str
    sourceCitation: str | None
    pageNumber: int | None


class AssessmentFindingBboxResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    xMin: float
    yMin: float
    xMax: float
    yMax: float


class AssessmentFindingResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    category: Literal["VCA_ANOMALY", "VCA_REPORT"]
    severity: Literal["INFO", "LOW"]
    message: str = Field(min_length=1)
    candidateId: str | None = None
    imageId: str | None = None
    conceptFamily: str | None = None
    descriptor: str | None = None
    citations: tuple[AssessmentFindingCitationResponse, ...] = ()
    bbox: AssessmentFindingBboxResponse | None = None
    # Vectorized mask outlines in original-image pixel space - (x, y) point
    # pairs, one polygon per disconnected mask fragment (real masks are
    # often multi-component). The mask is the standard segmentation signal;
    # bbox above is kept only for auxiliary/legacy display.
    polygons: tuple[tuple[tuple[float, float], ...], ...] | None = None


class RagQueryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    lane: str
    promptText: str
    queryId: str


class RagRetrievalResultResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunkId: str
    citationId: str
    lane: str
    matchedTerms: tuple[str, ...]
    pageNumber: int | None
    promptText: str
    queryId: str
    rank: int
    score: float
    snippetText: str
    sourceCitation: str


class RagEvidenceRowResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidenceState: str
    lane: str
    matchedCitationIds: tuple[str, ...]
    promptText: str
    queryId: str | None
    ragParentCandidateId: str
    topCitationId: str | None
    topRetrievalScore: float | None


class RagVisualConceptCardResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    conceptCardId: str
    conceptFamily: str | None
    contextTerms: tuple[str, ...]
    descriptorTerms: tuple[str, ...]
    materialTerms: tuple[str, ...]
    provenanceStrength: str
    ragParentCandidateId: str
    rawRetrievedSentence: str
    retrievalScore: float
    sourceCitationIds: tuple[str, ...]


class RagArtifactsResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    # alias 처리한 이유: 필드명을 그대로 `schema`로 두면 BaseModel의
    # (deprecated) .schema() 메서드를 가린다는 pydantic 경고가 발생한다.
    schema_: Literal["rag_candidate_evidence_v1"] = Field(alias="schema")
    queryCount: int
    retrievalResultCount: int
    evidenceRowCount: int
    visualConceptCardCount: int
    queries: tuple[RagQueryResponse, ...]
    retrievalResults: tuple[RagRetrievalResultResponse, ...]
    evidenceRows: tuple[RagEvidenceRowResponse, ...]
    visualConceptCards: tuple[RagVisualConceptCardResponse, ...]


class AssessmentReportResponse(AssessmentRunResponse):
    summary: str = Field(min_length=1)
    findings: tuple[AssessmentFindingResponse, ...]
    ragArtifacts: RagArtifactsResponse | None = None
