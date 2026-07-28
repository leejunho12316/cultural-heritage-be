from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from app.schemas.job_response import (
    StitchJobResponse,
    StitchJobStatusResponse,
)
from app.schemas.stitch_request import StitchJobRequest
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
    """Spring Boot가 공유 폴더에 저장한 결합 작업을 접수한다."""
    try:
        response, should_run = job_service.accept_job(request)
        if should_run:
            background_tasks.add_task(job_service.run_job, request)
        return response
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.get(
    "/jobs/{job_id}",
    response_model=StitchJobStatusResponse,
)
def get_stitch_job_status(job_id: str) -> StitchJobStatusResponse:
    try:
        return job_service.get_status(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
