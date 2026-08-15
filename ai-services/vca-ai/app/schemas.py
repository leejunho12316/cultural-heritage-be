from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok"]
    service: Literal["vca-ai"]
    mode: Literal["deterministic"]


class SystemInfoModelResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    repoId: str
    revision: str


class SystemInfoResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    os: str
    pythonVersion: str
    device: str
    libraries: dict[str, str]
    models: tuple[SystemInfoModelResponse, ...] = ()


class InputImageUrlRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    fileName: str = Field(min_length=1, max_length=255)
    url: str = Field(min_length=1, max_length=4096)


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
    # 둘 중 정확히 하나만 채워진다: inputImageFolder는 공유 마운트 경로가 있는 로컬 폴백용,
    # inputImageUrls는 S3 기반 저장소용(vca-ai가 이 URL들로 이미지를 직접 내려받는다).
    inputImageFolder: str | None = Field(default=None, min_length=1, max_length=4096)
    inputImageUrls: tuple[InputImageUrlRequest, ...] | None = None
    resumeFromProjectName: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )


class AssessmentStageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    status: str
    exitCode: int | None = None
    reason: str | None = None


class AssessmentStageProgressResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    completed: int
    total: int


class AssessmentRunResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    runId: str
    assessmentId: str
    status: Literal["RUNNING", "COMPLETED", "FAILED"]
    currentStage: str | None = None
    currentStageProgress: AssessmentStageProgressResponse | None = None
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
    # 원본 이미지 픽셀 공간의 벡터화된 마스크 윤곽선 - (x, y) 점 쌍이며,
    # 끊어진 마스크 조각마다 폴리곤 하나씩(실제 마스크는 다중 컴포넌트인
    # 경우가 흔함). 마스크가 기준이 되는 세그멘테이션 신호이고; 위의 bbox는
    # 보조/레거시 표시용으로만 남아 있다.
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
