"""Receipt payload helpers for startup orchestration."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import TYPE_CHECKING, Final, NotRequired, Protocol, TypedDict

from modules.shared import PathSafetyError, ensure_safe_run_root

if TYPE_CHECKING:
    from pathlib import Path

RECEIPT_SCHEMA: Final = "vca-startup-receipt-v1"
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


def _write_startup_receipt(
    request: _StartupReceiptRequest, payload: _StartupReceiptPayload
) -> None:
    receipt_path = _startup_receipt_path(request)
    _ = receipt_path.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _startup_receipt_path(request: _StartupReceiptRequest) -> Path:
    receipt_dir = _safe_receipt_dir(request)
    receipt_path = receipt_dir / "startup.json"
    if receipt_path.is_symlink():
        raise PathSafetyError(str(receipt_path), "startup receipt is a symlink")
    return ensure_safe_run_root(request.output_root, receipt_path)


def _safe_receipt_dir(request: _StartupReceiptRequest) -> Path:
    receipt_dir = request.output_root / "receipts"
    if receipt_dir.is_symlink():
        raise PathSafetyError(str(receipt_dir), "startup receipt dir is a symlink")
    safe_receipt_dir = ensure_safe_run_root(request.output_root, receipt_dir)
    receipt_dir.mkdir(parents=True, exist_ok=True)
    return safe_receipt_dir


StageReceiptPayload = _StageReceiptPayload
StartupStatus = _StartupStatus
stage_payload = _stage_payload
startup_payload = _startup_payload
write_startup_receipt = _write_startup_receipt
