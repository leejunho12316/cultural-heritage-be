from fastapi import APIRouter, HTTPException, status

from app.schemas import (
    AssessmentFindingResponse,
    AssessmentReportResponse,
    AssessmentRunCreateRequest,
    AssessmentRunResponse,
)
from app.services.assessment_runs import (
    AssessmentId,
    InputImageFolder,
    AssessmentReport,
    AssessmentRun,
    InvalidAssessmentRunIdError,
    InvalidInputImageFolderError,
    InvalidProjectNameError,
    ProjectName,
    VcaRunFailedError,
    VcaRuntimeSettingsError,
    create_assessment_run,
    get_assessment_report,
    get_assessment_run,
)
from app.services.vca_artifacts import VcaReportArtifactError


router = APIRouter(prefix="/internal/vca", tags=["internal-vca"])


@router.post(
    "/assessment-runs",
    response_model=AssessmentRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_run(request: AssessmentRunCreateRequest) -> AssessmentRunResponse:
    try:
        run = create_assessment_run(
            AssessmentId(request.assessmentId),
            ProjectName(request.projectName),
            InputImageFolder(request.inputImageFolder),
        )
    except (InvalidInputImageFolderError, InvalidProjectNameError) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
    except (VcaRunFailedError, VcaRuntimeSettingsError) as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error
    return _run_response(run)


@router.get("/assessment-runs/{run_id}", response_model=AssessmentRunResponse)
def get_run_status(run_id: str) -> AssessmentRunResponse:
    return _run_response(_known_run(run_id))


@router.get(
    "/assessment-runs/{run_id}/report",
    response_model=AssessmentReportResponse,
)
def get_run_report(run_id: str) -> AssessmentReportResponse:
    try:
        report = get_assessment_report(_known_run(run_id))
    except (VcaReportArtifactError, VcaRuntimeSettingsError) as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error
    return _report_response(report)


def _known_run(run_id: str) -> AssessmentRun:
    try:
        return get_assessment_run(run_id)
    except InvalidAssessmentRunIdError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error


def _run_response(run: AssessmentRun) -> AssessmentRunResponse:
    return AssessmentRunResponse(
        runId=run.run_id,
        assessmentId=run.assessment_id,
        status="COMPLETED",
    )


def _report_response(report: AssessmentReport) -> AssessmentReportResponse:
    return AssessmentReportResponse(
        runId=report.run.run_id,
        assessmentId=report.run.assessment_id,
        status="COMPLETED",
        summary=report.summary,
        findings=tuple(
            AssessmentFindingResponse(
                category=finding.category,
                severity=finding.severity,
                message=finding.message,
            )
            for finding in report.findings
        ),
    )
