import json
from pathlib import Path
from typing import Any

from app import config


class LocalStorage:
    """Process-local temporary job metadata; not shared and not authoritative."""

    def __init__(self, jobs_root: str | Path | None = None) -> None:
        self.jobs_root = Path(jobs_root or config.STITCH_JOBS_ROOT).resolve()

    def read_job(self, job_id: str) -> dict[str, Any]:
        path = self.get_job_file(job_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def write_job(self, job_id: str, job_data: dict[str, Any]) -> None:
        path = self.get_job_file(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(job_data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_job_file(self, job_id: str) -> Path:
        job_directory = (self.jobs_root / job_id).resolve()
        if job_directory.parent != self.jobs_root:
            raise ValueError(f"Invalid job id: {job_id}")
        return job_directory / "job.json"
