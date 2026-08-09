from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

if TYPE_CHECKING:
    from app.services.vca_rag_artifacts import VcaRagArtifacts


ArtifactModel = TypeVar("ArtifactModel", bound=BaseModel)
_DRY_RUN_SKIPPED_STAGES = frozenset(
    {"mask_refining", "anomaly_grouping", "report_generating"}
)
_DRY_RUN_SKIP_REASON_PREFIX = "skipped during startup dry-run"


@dataclass(frozen=True, slots=True)
class VcaReportArtifactError(Exception):
    project_name: str
    reason: str

    def __str__(self) -> str:
        return f"VCA report artifact error for {self.project_name}: {self.reason}"


@dataclass(frozen=True, slots=True)
class VcaReportFindingCitation:
    citation_id: str
    source_citation: str | None
    page_number: int | None


@dataclass(frozen=True, slots=True)
class VcaReportFindingBbox:
    x_min: float
    y_min: float
    x_max: float
    y_max: float


@dataclass(frozen=True, slots=True)
class VcaReportFinding:
    category: Literal["VCA_ANOMALY", "VCA_REPORT"]
    severity: Literal["INFO", "LOW"]
    message: str
    candidate_id: str | None = None
    image_id: str | None = None
    concept_family: str | None = None
    descriptor: str | None = None
    citations: tuple[VcaReportFindingCitation, ...] = ()
    bbox: VcaReportFindingBbox | None = None
    polygon: tuple[tuple[float, float], ...] | None = None


@dataclass(frozen=True, slots=True)
class VcaReportArtifacts:
    summary: str
    findings: tuple[VcaReportFinding, ...]
    rag: VcaRagArtifacts | None = None


@dataclass(frozen=True, slots=True)
class ReportArtifact:
    path: Path
    project_name: str
    artifact_name: str


class StartupStage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str = Field(min_length=1)
    status: Literal["completed", "skipped"]
    reason: str | None = None


class StartupReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    project_name: str = Field(min_length=1)
    status: Literal["completed"]
    stages: tuple[StartupStage, ...]


class DryRunPreprocessingManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    device: Literal["not_executed_dry_run"]
    detector_lane_status: Literal["dry_run_not_executed"]
    model_invocations: Literal[0]
    sam2_calls: Literal[0]


class FinalReportMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    final_success: bool


class FinalVerificationReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    verification_status: str = Field(min_length=1)


class TraceMetadataCitation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    citation_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    source_citation: str | None = None
    page_number: int | None = None
    score: float | None = None


class TraceMetadataBbox(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    x_min: float
    y_min: float
    x_max: float
    y_max: float


class TraceMetadataCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    candidate_id: str = Field(min_length=1)
    image_id: str = Field(min_length=1)
    concept_family: str = Field(min_length=1)
    hybrid_descriptor: str = Field(min_length=1)
    terminal_status: Literal["kept", "suppressed"]
    citations: tuple[TraceMetadataCitation, ...] = ()
    bbox: TraceMetadataBbox | None = None
    polygon: tuple[tuple[float, float], ...] | None = None


class TraceReportMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    candidates: tuple[TraceMetadataCandidate, ...]


class InputManifestImageEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    image_id: str = Field(min_length=1)
    file_sha256: str = Field(min_length=1)


class InputManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    images: tuple[InputManifestImageEntry, ...] = ()


# 엔진 산출물을 읽어 VcaReportArtifacts로 조립하는 진입점. dry-run이면
# _dry_run_report()로, 아니면 최종 리포트 메타데이터/검증 결과를 읽고
# findings와 RAG 산출물을 채운 리포트를 만든다.
# get_assessment_report()에서 호출된다.
def load_vca_report(
    engine_root: Path,
    project_name: str,
    *,
    is_dry_run: bool,
) -> VcaReportArtifacts:
    from app.services.vca_rag_artifacts import load_optional_vca_rag_artifacts

    output_root = engine_root / "output"
    startup = _startup_receipt(output_root, project_name)
    if is_dry_run:
        return _dry_run_report(output_root, project_name, startup)
    final_report_root = output_root / "report_generating" / project_name / "final_report"
    metadata = _read_required_artifact(
        ReportArtifact(
            final_report_root / "metadata.json", project_name, "final report metadata"
        ),
        FinalReportMetadata,
    )
    verification = _read_required_artifact(
        ReportArtifact(
            final_report_root / "verification" / "receipt.json",
            project_name,
            "final report verification receipt",
        ),
        FinalVerificationReceipt,
    )
    if not metadata.final_success:
        raise VcaReportArtifactError(
            project_name,
            "final report metadata did not mark the run successful",
        )
    if verification.verification_status != "pass":
        raise VcaReportArtifactError(
            project_name,
            "final report verification did not pass",
        )
    findings = _findings_from_candidates(output_root, project_name)
    rag = load_optional_vca_rag_artifacts(output_root, project_name)
    return VcaReportArtifacts(
        summary=f"VCA report for {project_name}: verification {verification.verification_status}.",
        findings=findings,
        rag=rag,
    )


# startup.json 영수증을 읽고, project_name이 일치하는지 확인한다.
def _startup_receipt(output_root: Path, project_name: str) -> StartupReceipt:
    startup = _read_required_artifact(
        ReportArtifact(
            output_root / "result" / project_name / "receipts" / "startup.json",
            project_name,
            "startup receipt",
        ),
        StartupReceipt,
    )
    if startup.project_name != project_name:
        raise VcaReportArtifactError(project_name, "startup receipt project name mismatch")
    return startup


# dry-run 모드 전용 리포트를 만든다. 무거운 스테이지들이 실제로 스킵되었는지와
# 전처리 증거 파일 존재 여부만 확인하고, 실제 findings는 생성하지 않는다.
def _dry_run_report(
    output_root: Path,
    project_name: str,
    startup: StartupReceipt,
) -> VcaReportArtifacts:
    skipped_stages = frozenset(
        stage.name
        for stage in startup.stages
        if (
            stage.status == "skipped"
            and stage.reason is not None
            and stage.reason.startswith(_DRY_RUN_SKIP_REASON_PREFIX)
        )
    )
    # 호출자의 is_dry_run 플래그만 믿지 않고 영수증 자체로 dry-run 여부를
    # 다시 확인한다: 무거운 스테이지들이 실제로 예상된 이유로 스킵되었는지
    # 검증함으로써, 최종 리포트 생성 전에 크래시난 real run과 구분한다.
    if not _DRY_RUN_SKIPPED_STAGES.issubset(skipped_stages):
        raise VcaReportArtifactError(
            project_name,
            "dry-run startup evidence is incomplete",
        )
    _ = _read_required_artifact(
        ReportArtifact(
            output_root
            / "preprocessing"
            / project_name
            / "manifests"
            / "real_preprocessing_manifest.json",
            project_name,
            "dry-run preprocessing evidence",
        ),
        DryRunPreprocessingManifest,
    )
    return VcaReportArtifacts(
        summary=(
            f"VCA dry-run completed for {project_name}; "
            "final report generation was intentionally skipped."
        ),
        findings=(
            VcaReportFinding(
                category="VCA_REPORT",
                severity="INFO",
                message=(
                    "Dry-run evidence verified; no final VCA report was generated "
                    f"for {project_name}."
                ),
            ),
        ),
    )


# finding은 여기서 점수가 가장 높은 인용만 노출한다; 후보의 다른 exported
# 인용들은 이 시점에서 버려지며 리포트 응답까지 도달하지 못한다.
_MAX_FINDING_CITATIONS: Final = 2


# report_generating 스테이지의 trace metadata에서 kept 상태인 후보만
# findings로 변환한다. metadata 파일이 없거나 kept 후보가 하나도 없으면
# "리포트는 생성되었다"는 플레이스홀더 finding 하나를 반환한다.
def _findings_from_candidates(
    output_root: Path, project_name: str
) -> tuple[VcaReportFinding, ...]:
    metadata_path = (
        output_root / "report_generating" / project_name / "report" / "metadata.json"
    )
    if not metadata_path.is_file():
        return (_report_available_finding(project_name),)
    metadata = _read_required_artifact(
        ReportArtifact(metadata_path, project_name, "report trace metadata"),
        TraceReportMetadata,
    )
    image_sha256_by_id = _image_sha256_by_id(output_root, project_name)
    findings = tuple(
        _finding_from_candidate(candidate, image_sha256_by_id)
        for candidate in metadata.candidates
        if candidate.terminal_status == "kept"
    )
    if findings:
        return findings
    return (_report_available_finding(project_name),)


def _image_sha256_by_id(output_root: Path, project_name: str) -> dict[str, str]:
    """엔진의 image_id를 업로드 파일의 sha256으로 변환하는 best-effort 조회.

    Spring의 업로드 이미지 기록은 파일의 content sha256만 알고, 엔진이
    경로+해시로 만든 자체 image_id는 모른다. 그래서 finding의 원본
    image_id는 Spring/FE 쪽 업로드 이미지와 절대 매칭될 수 없다.
    preprocessing 입력 매니페스트가 둘 다 기록하는 유일한 곳이므로 이를
    통해 변환한다. 매니페스트가 없거나 유효하지 않으면 전체 리포트를
    실패시키지 않고 변환 없이(엔진 image_id 그대로) 진행한다 - 이는
    정확성에 필수적인 값이 아니라 화면 표시용 상관관계 보조 값이기
    때문이다.
    """
    manifest_path = (
        output_root / "preprocessing" / project_name / "manifests" / "input_manifest.json"
    )
    try:
        payload = manifest_path.read_text(encoding="utf-8")
        manifest = InputManifest.model_validate_json(payload)
    except (OSError, ValidationError):
        return {}
    return {image.image_id: image.file_sha256 for image in manifest.images}


# 후보 하나를 finding으로 변환한다. image_id는 가능하면 업로드 파일
# sha256으로, 없으면 엔진 image_id 그대로 사용한다.
def _finding_from_candidate(
    candidate: TraceMetadataCandidate, image_sha256_by_id: dict[str, str]
) -> VcaReportFinding:
    return VcaReportFinding(
        category="VCA_ANOMALY",
        severity="INFO",
        message=f"{candidate.concept_family}: {candidate.hybrid_descriptor}",
        candidate_id=candidate.candidate_id,
        image_id=image_sha256_by_id.get(candidate.image_id, candidate.image_id),
        concept_family=candidate.concept_family,
        descriptor=candidate.hybrid_descriptor,
        citations=_top_citations(candidate.citations),
        bbox=_finding_bbox(candidate.bbox),
        polygon=candidate.polygon,
    )


def _finding_bbox(bbox: TraceMetadataBbox | None) -> VcaReportFindingBbox | None:
    if bbox is None:
        return None
    return VcaReportFindingBbox(bbox.x_min, bbox.y_min, bbox.x_max, bbox.y_max)


# exported 상태인 인용만 점수순으로 정렬해 상위 _MAX_FINDING_CITATIONS
# 개만 남긴다.
def _top_citations(
    citations: tuple[TraceMetadataCitation, ...],
) -> tuple[VcaReportFindingCitation, ...]:
    exported = [citation for citation in citations if citation.status == "exported"]
    ranked = sorted(exported, key=_citation_score, reverse=True)
    return tuple(
        VcaReportFindingCitation(
            citation_id=citation.citation_id,
            source_citation=citation.source_citation,
            page_number=citation.page_number,
        )
        for citation in ranked[:_MAX_FINDING_CITATIONS]
    )


def _citation_score(citation: TraceMetadataCitation) -> float:
    return citation.score if citation.score is not None else 0.0


def _report_available_finding(project_name: str) -> VcaReportFinding:
    return VcaReportFinding(
        category="VCA_REPORT",
        severity="INFO",
        message=f"Generated VCA report is available for {project_name}.",
    )


# 필수 산출물 JSON 파일을 읽어 pydantic 모델로 검증한다. 파일이 없거나
# 검증에 실패하면 VcaReportArtifactError를 던진다. load_vca_report() 계열
# 함수들에서 공통으로 사용된다.
def _read_required_artifact(
    artifact: ReportArtifact,
    model_type: type[ArtifactModel],
) -> ArtifactModel:
    try:
        payload = artifact.path.read_text(encoding="utf-8")
    except OSError as error:
        raise VcaReportArtifactError(
            artifact.project_name,
            f"required VCA report artifact is unavailable: {artifact.artifact_name}",
        ) from error
    try:
        return model_type.model_validate_json(payload)
    except ValidationError as error:
        raise VcaReportArtifactError(
            artifact.project_name,
            f"required VCA report artifact is invalid: {artifact.artifact_name}",
        ) from error
