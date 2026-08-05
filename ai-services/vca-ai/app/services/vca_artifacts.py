from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError


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
class VcaReportFinding:
    category: Literal["VCA_ANOMALY", "VCA_REPORT"]
    severity: Literal["INFO", "LOW"]
    message: str


@dataclass(frozen=True, slots=True)
class VcaReportArtifacts:
    summary: str
    findings: tuple[VcaReportFinding, ...]


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


class CandidateResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    candidate_id: str = Field(min_length=1)
    kept: bool


class AnomalyGroupingResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    candidate_results: tuple[CandidateResult, ...]


def load_vca_report(
    engine_root: Path,
    project_name: str,
    *,
    is_dry_run: bool,
) -> VcaReportArtifacts:
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
    return VcaReportArtifacts(
        summary=f"VCA report for {project_name}: verification {verification.verification_status}.",
        findings=findings,
    )


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


def _findings_from_candidates(
    output_root: Path, project_name: str
) -> tuple[VcaReportFinding, ...]:
    candidates_path = (
        output_root
        / "anomaly_grouping"
        / project_name
        / "anomaly_grouping_result.json"
    )
    if not candidates_path.is_file():
        return (_report_available_finding(project_name),)
    result = _read_required_artifact(
        ReportArtifact(candidates_path, project_name, "anomaly grouping result"),
        AnomalyGroupingResult,
    )
    findings = tuple(
        VcaReportFinding(
            category="VCA_ANOMALY",
            severity="INFO",
            message=f"Retained VCA anomaly candidate {candidate.candidate_id}.",
        )
        for candidate in result.candidate_results
        if candidate.kept
    )
    if findings:
        return findings
    return (_report_available_finding(project_name),)


def _report_available_finding(project_name: str) -> VcaReportFinding:
    return VcaReportFinding(
        category="VCA_REPORT",
        severity="INFO",
        message=f"Generated VCA report is available for {project_name}.",
    )


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
