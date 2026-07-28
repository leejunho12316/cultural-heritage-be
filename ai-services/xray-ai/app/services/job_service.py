from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import config
from app.schemas.job_response import (
    StitchJobResponse,
    StitchJobStatusResponse,
)
from app.schemas.stitch_request import StitchJobRequest
from app.services.stitcher import (
    StitchExecutionError,
    StitchExecutionResult,
    Stitcher,
)


class JobService:
    """작업 접수, 상태 저장, 결합 실행을 관리한다."""

    def __init__(
        self,
        stitcher: Stitcher | None = None,
        jobs_root: Path | None = None,
    ) -> None:
        self.stitcher = stitcher or Stitcher()
        self.jobs_root = (
            jobs_root or config.STITCH_JOBS_ROOT
        ).resolve()

    def accept_job(
        self,
        request: StitchJobRequest,
    ) -> tuple[StitchJobResponse, bool]:
        job_dir = self._validate_request_paths(request)
        job_data = self._read_job(job_dir)

        current_status = str(job_data.get("status", "PENDING")).upper()
        already_accepted = job_data.get("acceptedByAi") is True

        if already_accepted and current_status in {
            "PENDING",
            "RUNNING",
            "COMPLETED",
        }:
            return (
                StitchJobResponse(
                    jobId=request.jobId,
                    artifactId=request.artifactId,
                    status=current_status,
                    message=str(job_data.get("message", "")),
                ),
                False,
            )

        job_data.update(
            {
                "jobId": request.jobId,
                "artifactId": request.artifactId,
                "colorDirectory": request.colorDirectory,
                "xrayDirectory": request.xrayDirectory,
                "outputDirectory": request.outputDirectory,
                "configName": request.configName,
                "status": "PENDING",
                "message": "X-ray stitching job was accepted.",
                "resultUrl": None,
                "errorMessage": None,
                "acceptedByAi": True,
                "updatedAt": self._now(),
            }
        )
        self._write_job(job_dir, job_data)

        return (
            StitchJobResponse(
                jobId=request.jobId,
                artifactId=request.artifactId,
                status="PENDING",
                message="X-ray stitching job was accepted.",
            ),
            True,
        )

    def run_job(self, request: StitchJobRequest) -> None:
        job_dir = self._validate_request_paths(request)
        self._update_status(
            job_dir=job_dir,
            status="RUNNING",
            message="X-ray stitching is running.",
            error_message=None,
        )

        try:
            result = self.stitcher.run(request)
        except StitchExecutionError as exc:
            self._update_status(
                job_dir=job_dir,
                status="FAILED",
                message="X-ray stitching failed.",
                error_message=str(exc),
            )
            return
        except Exception as exc:
            self._update_status(
                job_dir=job_dir,
                status="FAILED",
                message="Unexpected X-ray stitching failure.",
                error_message=f"{type(exc).__name__}: {exc}",
            )
            return

        self._mark_completed(job_dir, result)

    def get_status(self, job_id: str) -> StitchJobStatusResponse:
        job_dir = self._job_dir(job_id)
        job_data = self._read_job(job_dir)

        return StitchJobStatusResponse(
            jobId=str(job_data.get("jobId", job_id)),
            artifactId=str(job_data.get("artifactId", "")),
            status=str(job_data.get("status", "UNKNOWN")),
            message=str(job_data.get("message", "")),
            resultUrl=self._optional_string(job_data.get("resultUrl")),
            errorMessage=self._optional_string(
                job_data.get("errorMessage")
            ),
        )

    def _mark_completed(
        self,
        job_dir: Path,
        result: StitchExecutionResult,
    ) -> None:
        job_data = self._read_job(job_dir)
        job_data.update(
            {
                "status": "COMPLETED",
                "message": "X-ray stitching completed.",
                "resultUrl": f"/api/jobs/{job_dir.name}/result",
                "errorMessage": None,
                "returnCode": result.return_code,
                "outputDirectory": result.output_dir,
                "reportPath": result.report_path,
                "layoutPath": result.layout_path,
                "assembledImagePath": result.assembled_image_path,
                "stdoutLog": result.stdout_log,
                "stderrLog": result.stderr_log,
                "updatedAt": self._now(),
            }
        )
        self._write_job(job_dir, job_data)

    def _update_status(
        self,
        job_dir: Path,
        status: str,
        message: str,
        error_message: str | None,
    ) -> None:
        job_data = self._read_job(job_dir)
        job_data.update(
            {
                "status": status,
                "message": message,
                "errorMessage": error_message,
                "updatedAt": self._now(),
            }
        )
        self._write_job(job_dir, job_data)

    def _validate_request_paths(
        self,
        request: StitchJobRequest,
    ) -> Path:
        job_dir = self._job_dir(request.jobId)
        expected_color = (job_dir / "inputs" / "color").resolve()
        expected_xray = (job_dir / "inputs" / "xray").resolve()
        expected_output = (job_dir / "outputs").resolve()

        actual_color = Path(request.colorDirectory).resolve()
        actual_xray = Path(request.xrayDirectory).resolve()
        actual_output = Path(request.outputDirectory).resolve()

        if actual_color != expected_color:
            raise ValueError(f"Invalid colorDirectory: {actual_color}")
        if actual_xray != expected_xray:
            raise ValueError(f"Invalid xrayDirectory: {actual_xray}")
        if actual_output != expected_output:
            raise ValueError(f"Invalid outputDirectory: {actual_output}")
        if not expected_color.is_dir():
            raise FileNotFoundError(
                f"Color input directory was not found: {expected_color}"
            )
        if not expected_xray.is_dir():
            raise FileNotFoundError(
                f"X-ray input directory was not found: {expected_xray}"
            )

        expected_output.mkdir(parents=True, exist_ok=True)
        return job_dir

    def _job_dir(self, job_id: str) -> Path:
        try:
            parsed = uuid.UUID(job_id)
        except ValueError as exc:
            raise ValueError(f"Invalid jobId: {job_id}") from exc

        canonical_job_id = str(parsed)
        if canonical_job_id != job_id.lower():
            raise ValueError(f"Invalid jobId: {job_id}")

        job_dir = (self.jobs_root / canonical_job_id).resolve()
        if job_dir.parent != self.jobs_root:
            raise ValueError(f"Invalid jobId: {job_id}")
        if not job_dir.is_dir():
            raise FileNotFoundError(
                f"X-ray stitching job was not found: {job_id}"
            )
        return job_dir

    @staticmethod
    def _read_job(job_dir: Path) -> dict[str, Any]:
        job_path = job_dir / "job.json"
        if not job_path.is_file():
            raise FileNotFoundError(f"job.json was not found: {job_path}")

        try:
            data = json.loads(job_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid job.json: {job_path}") from exc

        if not isinstance(data, dict):
            raise ValueError(
                f"job.json must contain an object: {job_path}"
            )
        return data

    @staticmethod
    def _write_job(
        job_dir: Path,
        job_data: dict[str, Any],
    ) -> None:
        job_path = job_dir / "job.json"
        temp_path = job_dir / "job.json.tmp"
        temp_path.write_text(
            json.dumps(job_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(job_path)

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        return None if value is None else str(value)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
