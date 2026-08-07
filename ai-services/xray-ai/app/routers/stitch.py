from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from app.schemas.job_response import StitchJobResponse, StitchJobStatusResponse
from app.schemas.stitch_request import FinalizationJobRequest, StitchJobRequest
from app.services.job_service import JobService


router = APIRouter(tags=["X-ray Stitch"])
job_service = JobService()


@router.post(
    "/stitch/jobs",
    response_model=StitchJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_stitch_job(
    request: StitchJobRequest,
    background_tasks: BackgroundTasks,
) -> StitchJobResponse:
    """Accept an S3 URL job. Inputs and outputs never use a shared volume."""
    try:
        response, should_run = job_service.accept_stitch_job(request)
        if should_run:
            background_tasks.add_task(job_service.run_stitch_job, request)
        return response
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@router.post(
    "/finalize/jobs",
    response_model=StitchJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_finalization_job(
    request: FinalizationJobRequest,
    background_tasks: BackgroundTasks,
) -> StitchJobResponse:
    try:
        response = job_service.accept_finalization_job(request)
        background_tasks.add_task(job_service.run_finalization_job, request)
        return response
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@router.get("/jobs/{job_id}", response_model=StitchJobStatusResponse)
def get_stitch_job_status(job_id: str) -> StitchJobStatusResponse:
    try:
        return job_service.get_status(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
