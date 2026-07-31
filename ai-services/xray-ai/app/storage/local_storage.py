import json
from pathlib import Path
from typing import Any

from app import config


class LocalStorage:
    """Spring Boot와 FastAPI가 공유하는 작업 폴더를 관리한다."""

    def __init__(self, jobs_root: str | Path | None = None) -> None:
        self.jobs_root = Path(
            jobs_root or config.STITCH_JOBS_ROOT
        ).resolve()

    def read_job(self, job_id: str) -> dict[str, Any]:
        job_file = self.get_job_file(job_id)
        if not job_file.is_file():
            raise FileNotFoundError(f"job.json이 없습니다: {job_file}")
        with job_file.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError(f"job.json 최상위 값이 객체가 아닙니다: {job_file}")
        return data

    def write_job(self, job_id: str, job_data: dict[str, Any]) -> None:
        job_file = self.get_job_file(job_id)
        job_file.parent.mkdir(parents=True, exist_ok=True)
        temporary_file = job_file.with_suffix(".json.tmp")
        with temporary_file.open("w", encoding="utf-8") as file:
            json.dump(job_data, file, ensure_ascii=False, indent=2)
        temporary_file.replace(job_file)

    def get_job_file(self, job_id: str) -> Path:
        job_directory = (self.jobs_root / job_id).resolve()
        try:
            job_directory.relative_to(self.jobs_root)
        except ValueError as exc:
            raise ValueError(
                f"공유 작업 폴더 밖의 경로는 사용할 수 없습니다: {job_directory}"
            ) from exc
        return job_directory / "job.json"
