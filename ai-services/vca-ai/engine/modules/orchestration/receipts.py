"""Receipt payload helpers for startup orchestration."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import TYPE_CHECKING, Final, NotRequired, Protocol, TypedDict

from modules.shared import PathSafetyError, ensure_safe_run_root

if TYPE_CHECKING:
    from pathlib import Path

RECEIPT_SCHEMA: Final = "vca-startup-receipt-v1"
PROGRESS_SCHEMA: Final = "vca-startup-progress-v1"
STAGE_NAMES: Final = (
    "preprocessing",
    "rough_masking",
    "visual_cue_generation",
    "rag",
    "prompt_generating",
    "mask_refining",
    "anomaly_grouping",
    "report_generating",
)


class _StartupStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class _StageReceiptPayload(TypedDict):
    name: str
    status: str
    output_dir: str
    exit_code: NotRequired[int]
    reason: NotRequired[str]


class _StartupReceiptRequest(Protocol):
    @property
    def project_name(self) -> str: ...

    @property
    def input_image_folder(self) -> Path: ...

    @property
    def output_root(self) -> Path: ...

    @property
    def image_paths(self) -> tuple[Path, ...]: ...


class _StartupReceiptPayload(TypedDict):
    schema: str
    project_name: str
    status: str
    output_root: str
    input_image_folder: str
    image_count: int
    failed_stage: str | None
    stages: list[_StageReceiptPayload]


class _StartupProgressPayload(TypedDict):
    schema: str
    project_name: str
    status: str
    current_stage: str | None
    stages: list[_StageReceiptPayload]


# 스테이지 하나의 실행 결과를 리시트에 담을 payload dict로 변환한다.
# stage_execution.py의 실행 루프에서 스테이지가 끝날 때마다 호출된다.
def _stage_payload(
    name: str,
    status: _StartupStatus,
    output_dir: Path,
    *,
    exit_code: int | None = None,
    reason: str | None = None,
) -> _StageReceiptPayload:
    payload: _StageReceiptPayload = {
        "name": name,
        "status": status.value,
        "output_dir": str(output_dir),
    }
    if exit_code is not None:
        payload["exit_code"] = exit_code
    if reason is not None:
        payload["reason"] = reason
    return payload


# 전체 스타트업 실행 결과를 최종 startup.json 리시트 payload로 조립한다.
# startup.py에서 모든 스테이지 실행이 끝난 뒤 한 번 호출된다.
def _startup_payload(
    request: _StartupReceiptRequest,
    status: _StartupStatus,
    stages: list[_StageReceiptPayload],
    failed_stage: str | None,
) -> _StartupReceiptPayload:
    return {
        "schema": RECEIPT_SCHEMA,
        "project_name": request.project_name,
        "status": status.value,
        "output_root": str(request.output_root),
        "input_image_folder": str(request.input_image_folder),
        "image_count": len(request.image_paths),
        "failed_stage": failed_stage,
        "stages": stages,
    }


# 조립된 startup 리시트를 receipts/startup.json 파일에 기록한다.
# startup.py의 _execute에서 실행이 끝난 뒤 호출된다.
def _write_startup_receipt(
    request: _StartupReceiptRequest, payload: _StartupReceiptPayload
) -> None:
    receipt_path = _startup_receipt_path(request)
    _ = receipt_path.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _startup_receipt_path(request: _StartupReceiptRequest) -> Path:
    return _receipt_file_path(request.output_root, "startup.json")


# receipts 디렉터리 하위 파일 경로를 심볼릭 링크 여부까지 검증해 반환한다.
# output_root 밖으로 벗어나는 경로 조작을 막기 위한 안전 장치다.
def _receipt_file_path(output_root: Path, filename: str) -> Path:
    receipt_dir = _safe_receipt_dir(output_root)
    receipt_path = receipt_dir / filename
    if receipt_path.is_symlink():
        raise PathSafetyError(str(receipt_path), f"{filename} is a symlink")
    return ensure_safe_run_root(output_root, receipt_path)


# receipts 디렉터리를 심볼릭 링크 검증 후 생성하고 안전한 경로를 반환한다.
# _receipt_file_path에서 사용된다.
def _safe_receipt_dir(output_root: Path) -> Path:
    receipt_dir = output_root / "receipts"
    if receipt_dir.is_symlink():
        raise PathSafetyError(str(receipt_dir), "startup receipt dir is a symlink")
    safe_receipt_dir = ensure_safe_run_root(output_root, receipt_dir)
    receipt_dir.mkdir(parents=True, exist_ok=True)
    return safe_receipt_dir


# 진행 중인 스타트업 상태를 progress.json에 쓸 payload로 조립한다.
# stage_execution.py의 _write_progress에서 스테이지 전환마다 호출된다.
def _progress_payload(
    project_name: str,
    status: str,
    current_stage: str | None,
    stages: list[_StageReceiptPayload],
) -> _StartupProgressPayload:
    return {
        "schema": PROGRESS_SCHEMA,
        "project_name": project_name,
        "status": status,
        "current_stage": current_stage,
        "stages": stages,
    }


def _write_startup_progress(
    output_root: Path, payload: _StartupProgressPayload
) -> None:
    """Overwrite the in-flight progress snapshot atomically.

    Called after every stage transition so a concurrent reader (the vca-ai
    adapter polling on behalf of the frontend) always sees either the
    previous complete snapshot or the new one, never a partial write.
    """
    progress_path = _startup_progress_path(output_root)
    temporary = progress_path.with_suffix(f"{progress_path.suffix}.tmp")
    _ = temporary.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    _ = temporary.replace(progress_path)


def _startup_progress_path(output_root: Path) -> Path:
    return _receipt_file_path(output_root, "progress.json")


StageReceiptPayload = _StageReceiptPayload
StartupStatus = _StartupStatus
StartupProgressPayload = _StartupProgressPayload
stage_payload = _stage_payload
startup_payload = _startup_payload
write_startup_receipt = _write_startup_receipt
progress_payload = _progress_payload
write_startup_progress = _write_startup_progress
