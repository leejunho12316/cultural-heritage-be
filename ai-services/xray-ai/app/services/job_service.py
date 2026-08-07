from __future__ import annotations

import json
import shutil
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import config
from app.schemas.job_response import StitchJobResponse, StitchJobStatusResponse
from app.schemas.stitch_request import (
    FinalizationJobRequest,
    LocalStitchJobRequest,
    StitchJobRequest,
)
from app.services.finalizer import FinalizationError, Finalizer
from app.services.remote_io import RemoteIoError, callback, download, safe_extract_zip, upload
from app.services.stitcher import StitchExecutionError, StitchExecutionResult, Stitcher


class JobService:
    """S3 URL 기반 결합·최종화를 process-local workspace에서 실행한다."""

    def __init__(
        self,
        stitcher: Stitcher | None = None,
        finalizer: Finalizer | None = None,
        jobs_root: Path | None = None,
    ) -> None:
        self.stitcher = stitcher or Stitcher()
        self.jobs_root = (jobs_root or config.STITCH_JOBS_ROOT).resolve()
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.finalizer = finalizer or Finalizer(self.jobs_root)
        self._lock = threading.RLock()

    def accept_stitch_job(self, request: StitchJobRequest) -> tuple[StitchJobResponse, bool]:
        self._validate_uuid(request.jobId, "jobId")
        self._validate_uuid(request.artifactId, "artifactId")
        with self._lock:
            current = self._read_status_optional(request.jobId)
            if current and str(current.get("status", "")).upper() in {"PENDING", "RUNNING"}:
                return self._response_from_status(current), False
            self._write_status(
                request.jobId,
                {
                    "jobId": request.jobId,
                    "artifactId": request.artifactId,
                    "status": "PENDING",
                    "message": "X-ray stitching job was accepted.",
                    "errorMessage": None,
                    "updatedAt": self._now(),
                },
            )
        return (
            StitchJobResponse(
                jobId=request.jobId,
                artifactId=request.artifactId,
                status="PENDING",
                message="X-ray stitching job was accepted.",
            ),
            True,
        )

    def run_stitch_job(self, request: StitchJobRequest) -> None:
        job_dir = self._reset_job_dir(request.jobId)
        self._set_status(
            request.jobId,
            request.artifactId,
            "RUNNING",
            "X-ray stitching is running.",
            None,
        )
        try:
            color_dir = job_dir / "inputs" / "color"
            xray_dir = job_dir / "inputs" / "xray"
            output_dir = job_dir / "outputs"
            color_dir.mkdir(parents=True, exist_ok=True)
            xray_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            download(
                str(request.colorInput.downloadUrl),
                color_dir / self._safe_name(request.colorInput.fileName),
                config.REMOTE_MAX_IMAGE_BYTES,
            )
            for item in request.xrayInputs:
                download(
                    str(item.downloadUrl),
                    xray_dir / self._safe_name(item.fileName),
                    config.REMOTE_MAX_IMAGE_BYTES,
                )

            local_request = LocalStitchJobRequest(
                jobId=request.jobId,
                artifactId=request.artifactId,
                colorDirectory=str(color_dir),
                xrayDirectory=str(xray_dir),
                outputDirectory=str(output_dir),
                configName=request.configName,
            )
            self._write_job_json(job_dir, request, "RUNNING")
            result = self.stitcher.run(local_request)
            self._write_completed_job_json(job_dir, request, result)

            artifact_dir = Path(result.layout_path).resolve().parent
            bundle_path = job_dir / "finalization_bundle.zip"
            self._create_finalization_bundle(job_dir, artifact_dir, bundle_path)

            upload(str(request.outputPutUrls.assembled), Path(result.assembled_image_path), "image/png")
            upload(str(request.outputPutUrls.layout), Path(result.layout_path), "application/json")
            upload(str(request.outputPutUrls.report), Path(result.report_path), "application/json")
            upload(str(request.outputPutUrls.finalizationBundle), bundle_path, "application/zip")

            self._set_status(
                request.jobId,
                request.artifactId,
                "COMPLETED",
                "X-ray stitching completed.",
                None,
                result_url=f"/api/jobs/{request.jobId}",
            )
            # S3 outputs are the source of truth. A transient callback failure must
            # not turn a completed job into FAILED; Spring can recover via /reconcile.
            self._send_callback_best_effort(
                request.callbackUrl,
                request.callbackToken,
                request.jobId,
                request.artifactId,
                "COMPLETED",
                "X-ray stitching completed.",
                None,
            )
        except (RemoteIoError, StitchExecutionError, OSError, ValueError) as exc:
            self._fail_and_callback(request, exc)
        except Exception as exc:  # keep background task from disappearing silently
            self._fail_and_callback(request, RuntimeError(f"{type(exc).__name__}: {exc}"))

    def accept_finalization_job(
        self,
        request: FinalizationJobRequest,
    ) -> StitchJobResponse:
        self._validate_uuid(request.jobId, "jobId")
        self._validate_uuid(request.artifactId, "artifactId")
        self._set_status(
            request.jobId,
            request.artifactId,
            "FINALIZING",
            "Final X-ray rendering was accepted.",
            None,
        )
        return StitchJobResponse(
            jobId=request.jobId,
            artifactId=request.artifactId,
            status="FINALIZING",
            message="Final X-ray rendering was accepted.",
        )

    def run_finalization_job(self, request: FinalizationJobRequest) -> None:
        staging = self.jobs_root / f"{request.jobId}.finalizing"
        staging = staging.resolve()
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)
        bundle_path = staging / "bundle.zip"

        try:
            download(str(request.bundleDownloadUrl), bundle_path, config.REMOTE_MAX_BUNDLE_BYTES)
            extracted = staging / "extracted"
            safe_extract_zip(
                bundle_path,
                extracted,
                config.REMOTE_MAX_BUNDLE_UNCOMPRESSED_BYTES,
            )

            final_job_dir = (self.jobs_root / request.jobId).resolve()
            if final_job_dir.exists():
                shutil.rmtree(final_job_dir)
            # Bundle entries are relative to the job directory.
            shutil.move(str(extracted), str(final_job_dir))
            # The move replaces the directory that held status.json. Restore the
            # process-local status immediately so GET /api/jobs/{jobId} remains
            # available while final rendering is running.
            self._set_status(
                request.jobId,
                request.artifactId,
                "FINALIZING",
                "Final X-ray rendering is running.",
                None,
            )

            artifact_dir = (
                final_job_dir
                / "outputs"
                / "assembly"
                / "artifacts"
                / request.artifactId
            )
            if not artifact_dir.is_dir():
                raise FinalizationError(
                    f"Artifact directory is missing from bundle: {artifact_dir}"
                )
            download(
                str(request.finalLayoutDownloadUrl),
                artifact_dir / "layout.final.json",
                config.REMOTE_MAX_JSON_BYTES,
            )

            # Finalizer deliberately requires COMPLETED in job.json.
            job_json = final_job_dir / "job.json"
            job_data = self._read_json(job_json)
            job_data.update(
                {
                    "jobId": request.jobId,
                    "artifactId": request.artifactId,
                    "status": "COMPLETED",
                }
            )
            self._write_json(job_json, job_data)

            self.finalizer.finalize(request.jobId)
            outputs = request.outputPutUrls
            upload(str(outputs.assembledFinal), artifact_dir / "assembled_xray.final.png", "image/png")
            upload(str(outputs.sourceOwner), artifact_dir / "source_owner.final.png", "image/png")
            upload(str(outputs.fragmentOwner), artifact_dir / "fragment_owner.final.png", "image/png")
            upload(str(outputs.seamZone), artifact_dir / "seam_zone.final.png", "image/png")
            upload(str(outputs.overlapMask), artifact_dir / "overlap_mask.final.png", "image/png")
            upload(str(outputs.provenance), artifact_dir / "provenance.final.json", "application/json")

            self._set_status(
                request.jobId,
                request.artifactId,
                "FINALIZED",
                "Final X-ray rendering completed.",
                None,
            )
            # Keep FINALIZED even when the callback is temporarily unavailable.
            self._send_callback_best_effort(
                request.callbackUrl,
                request.callbackToken,
                request.jobId,
                request.artifactId,
                "FINALIZED",
                "Final X-ray rendering completed.",
                None,
            )
        except (RemoteIoError, FinalizationError, OSError, ValueError) as exc:
            self._set_status(
                request.jobId,
                request.artifactId,
                "FAILED",
                "Final X-ray rendering failed.",
                str(exc),
            )
            self._send_callback_best_effort(
                request.callbackUrl,
                request.callbackToken,
                request.jobId,
                request.artifactId,
                "FAILED",
                "Final X-ray rendering failed.",
                str(exc),
            )
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            self._set_status(
                request.jobId,
                request.artifactId,
                "FAILED",
                "Unexpected final X-ray rendering failure.",
                message,
            )
            self._send_callback_best_effort(
                request.callbackUrl,
                request.callbackToken,
                request.jobId,
                request.artifactId,
                "FAILED",
                "Unexpected final X-ray rendering failure.",
                message,
            )
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def get_status(self, job_id: str) -> StitchJobStatusResponse:
        self._validate_uuid(job_id, "jobId")
        data = self._read_status_optional(job_id)
        if data is None:
            raise FileNotFoundError(f"X-ray job status was not found: {job_id}")
        return StitchJobStatusResponse(
            jobId=str(data.get("jobId", job_id)),
            artifactId=str(data.get("artifactId", "")),
            status=str(data.get("status", "UNKNOWN")),
            message=str(data.get("message", "")),
            resultUrl=self._optional_string(data.get("resultUrl")),
            errorMessage=self._optional_string(data.get("errorMessage")),
        )

    def _fail_and_callback(self, request: StitchJobRequest, exc: Exception) -> None:
        message = str(exc)
        self._set_status(
            request.jobId,
            request.artifactId,
            "FAILED",
            "X-ray stitching failed.",
            message,
        )
        self._send_callback_best_effort(
            request.callbackUrl,
            request.callbackToken,
            request.jobId,
            request.artifactId,
            "FAILED",
            "X-ray stitching failed.",
            message,
        )

    def _reset_job_dir(self, job_id: str) -> Path:
        job_dir = (self.jobs_root / job_id).resolve()
        if job_dir.parent != self.jobs_root:
            raise ValueError(f"Invalid jobId: {job_id}")
        status_path = self._status_path(job_id)
        status_data = self._read_json(status_path) if status_path.is_file() else None
        if job_dir.exists():
            shutil.rmtree(job_dir)
        job_dir.mkdir(parents=True, exist_ok=True)
        if status_data is not None:
            self._write_json(job_dir / "status.json", status_data)
        return job_dir

    def _create_finalization_bundle(
        self,
        job_dir: Path,
        artifact_dir: Path,
        bundle_path: Path,
    ) -> None:
        required = [
            job_dir / "job.json",
            job_dir / "inputs" / "xray",
            artifact_dir / "layout.json",
            artifact_dir / "report.json",
        ]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise StitchExecutionError(
                "Finalization bundle inputs are missing: " + ", ".join(missing)
            )
        with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(job_dir / "job.json", "job.json")
            for path in (job_dir / "inputs" / "xray").rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(job_dir).as_posix())
            for path in artifact_dir.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(job_dir).as_posix())

    def _write_job_json(
        self,
        job_dir: Path,
        request: StitchJobRequest,
        status: str,
    ) -> None:
        self._write_json(
            job_dir / "job.json",
            {
                "jobId": request.jobId,
                "artifactId": request.artifactId,
                "status": status,
                "message": "X-ray stitching is running.",
                "updatedAt": self._now(),
            },
        )

    def _write_completed_job_json(
        self,
        job_dir: Path,
        request: StitchJobRequest,
        result: StitchExecutionResult,
    ) -> None:
        self._write_json(
            job_dir / "job.json",
            {
                "jobId": request.jobId,
                "artifactId": request.artifactId,
                "status": "COMPLETED",
                "message": "X-ray stitching completed.",
                "outputDirectory": result.output_dir,
                "reportPath": result.report_path,
                "layoutPath": result.layout_path,
                "assembledImagePath": result.assembled_image_path,
                "stdoutLog": result.stdout_log,
                "stderrLog": result.stderr_log,
                "updatedAt": self._now(),
            },
        )

    def _set_status(
        self,
        job_id: str,
        artifact_id: str,
        status: str,
        message: str,
        error_message: str | None,
        result_url: str | None = None,
    ) -> None:
        with self._lock:
            self._write_status(
                job_id,
                {
                    "jobId": job_id,
                    "artifactId": artifact_id,
                    "status": status,
                    "message": message,
                    "resultUrl": result_url,
                    "errorMessage": error_message,
                    "updatedAt": self._now(),
                },
            )

    def _status_path(self, job_id: str) -> Path:
        path = (self.jobs_root / job_id / "status.json").resolve()
        if path.parent.parent != self.jobs_root:
            raise ValueError(f"Invalid jobId: {job_id}")
        return path

    def _write_status(self, job_id: str, data: dict[str, Any]) -> None:
        path = self._status_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json(path, data)

    def _read_status_optional(self, job_id: str) -> dict[str, Any] | None:
        path = self._status_path(job_id)
        return self._read_json(path) if path.is_file() else None

    @staticmethod
    def _response_from_status(data: dict[str, Any]) -> StitchJobResponse:
        return StitchJobResponse(
            jobId=str(data.get("jobId", "")),
            artifactId=str(data.get("artifactId", "")),
            status=str(data.get("status", "UNKNOWN")),
            message=str(data.get("message", "")),
        )

    @staticmethod
    def _safe_name(value: str) -> str:
        result = str(value).replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not result or result in {".", ".."}:
            raise ValueError(f"Invalid file name: {value!r}")
        return result

    @staticmethod
    def _validate_uuid(value: str, label: str) -> None:
        try:
            parsed = uuid.UUID(value)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid {label}: {value!r}") from exc
        if str(parsed) != value.lower():
            raise ValueError(f"Non-canonical {label}: {value!r}")

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"JSON object expected: {path}")
        return data

    def _send_callback(
        self,
        url: Any,
        token: str | None,
        job_id: str,
        artifact_id: str,
        status: str,
        message: str,
        error_message: str | None,
    ) -> None:
        callback(
            str(url),
            {
                "jobId": job_id,
                "artifactId": artifact_id,
                "status": status,
                "message": message,
                "errorMessage": error_message,
            },
            token,
        )

    def _send_callback_best_effort(self, *args: Any) -> None:
        try:
            self._send_callback(*args)
        except RemoteIoError as exc:
            print(f"[WARN] callback delivery failed; Spring can reconcile from S3: {exc}")

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        return None if value is None else str(value)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
