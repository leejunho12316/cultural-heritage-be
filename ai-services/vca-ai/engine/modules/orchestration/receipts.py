"""Receipt payload helpers for startup orchestration."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import TYPE_CHECKING, Final, NotRequired, Protocol, TypedDict

from modules.shared import (
    ContractValidationError,
    StageProgressCount,
    receipt_file_path,
)
from modules.shared import update_stage_progress_count as _update_stage_progress_count
from modules.shared.json_object import parse_json_object_for_field

if TYPE_CHECKING:
    from pathlib import Path

    from modules.shared import FinalSuccessEvaluation
    from modules.shared.json_object import JsonObject

RECEIPT_SCHEMA: Final = "vca-startup-receipt-v1"
PROGRESS_SCHEMA: Final = "vca-startup-progress-v1"
STAGE_NAMES: Final = (
    "preprocessing",
    "rough_masking",
    "visual_cue_generation",
    "rag",
    "anomaly_grouping",
    "prompt_generating",
    "mask_refining",
    "report_trace_assembly",
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
    run_status: str
    final_success: bool


class _StartupProgressPayload(TypedDict):
    schema: str
    project_name: str
    status: str
    current_stage: str | None
    current_stage_progress: StageProgressCount | None
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
# startup.py에서 모든 스테이지 실행이 끝난 뒤 한 번 호출된다. run_status/
# final_success는 기존 status(completed/failed)보다 더 세분화된 판정(성공/
# 예산 차단/리포트만 실패/유효 후보 없음/그 외 실패/dry-run)을 담는 추가
# 필드다 - 기존 status 필드는 하위 호환을 위해 그대로 둔다.
def _startup_payload(
    request: _StartupReceiptRequest,
    status: _StartupStatus,
    stages: list[_StageReceiptPayload],
    failed_stage: str | None,
    evaluation: FinalSuccessEvaluation,
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
        "run_status": evaluation.status.value,
        "final_success": evaluation.final_success,
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
    return receipt_file_path(request.output_root, "startup.json")


# execute_startup_stages의 resume_from_stage 처리에서 호출된다. vca-ai가
# 이전에 실패한 run의 완료된 스테이지 산출물을(그 run의 startup.json과 함께)
# 이 run의 output_root로 미리 복사해뒀다는 전제 아래, resume_from_stage 이전
# 스테이지들의 완료 payload를 그대로 읽어와 재사용한다 - 그 스테이지들을
# 실제로 다시 돌리지 않고도 최종 리시트가 8단계 전체 이력을 온전히 담을 수
# 있게 하기 위함이다. 전제가 깨져 있으면(리시트 없음/파싱 실패/completed로
# 기록 안 됨) 조용히 넘어가지 않고 계약 오류로 실패시킨다 - vca-ai가 이미 이
# 전제를 확인한 뒤에만 --resume-from-stage를 보내야 하기 때문이다.
def _read_prior_completed_stages(
    output_root: Path, resume_from_stage: str
) -> list[_StageReceiptPayload]:
    field = "resume_from_stage"
    receipt_path = receipt_file_path(output_root, "startup.json")
    try:
        raw = receipt_path.read_text(encoding="utf-8")
    except OSError as error:
        reason = f"no prior startup receipt found to resume from ({error})"
        raise ContractValidationError(field, reason) from error
    payload = parse_json_object_for_field(raw, field)
    raw_stages = payload.get("stages")
    if not isinstance(raw_stages, list):
        reason = "prior startup receipt has no stages list"
        raise ContractValidationError(field, reason)
    by_name = {
        stage["name"]: stage
        for stage in raw_stages
        if isinstance(stage, dict) and isinstance(stage.get("name"), str)
    }
    if resume_from_stage not in STAGE_NAMES:
        reason = f"{resume_from_stage!r} is not a known stage"
        raise ContractValidationError(field, reason)
    boundary = STAGE_NAMES.index(resume_from_stage)
    prior_stages: list[_StageReceiptPayload] = []
    for name in STAGE_NAMES[:boundary]:
        stage = by_name.get(name)
        completed = _StartupStatus.COMPLETED.value
        if not isinstance(stage, dict) or stage.get("status") != completed:
            reason = f"prior stage {name!r} is not recorded as completed"
            raise ContractValidationError(field, reason)
        prior_stages.append(_typed_stage_payload(stage, field))
    return prior_stages


def _typed_stage_payload(stage: JsonObject, field: str) -> _StageReceiptPayload:
    name = stage.get("name")
    status = stage.get("status")
    output_dir = stage.get("output_dir")
    if (
        not isinstance(name, str)
        or not isinstance(status, str)
        or not isinstance(output_dir, str)
    ):
        reason = "prior stage payload is malformed"
        raise ContractValidationError(field, reason)
    payload: _StageReceiptPayload = {
        "name": name,
        "status": status,
        "output_dir": output_dir,
    }
    exit_code = stage.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        payload["exit_code"] = exit_code
    reason_value = stage.get("reason")
    if isinstance(reason_value, str):
        payload["reason"] = reason_value
    return payload


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
        "current_stage_progress": None,
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
    return receipt_file_path(output_root, "progress.json")


StageReceiptPayload = _StageReceiptPayload
StartupStatus = _StartupStatus
StartupProgressPayload = _StartupProgressPayload
stage_payload = _stage_payload
startup_payload = _startup_payload
write_startup_receipt = _write_startup_receipt
progress_payload = _progress_payload
write_startup_progress = _write_startup_progress
update_stage_progress_count = _update_stage_progress_count
read_prior_completed_stages = _read_prior_completed_stages
