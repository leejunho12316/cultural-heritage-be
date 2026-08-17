import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Final, assert_never

from app.services.assessment_models import AssessmentId, AssessmentRun
from app.services.vca_runtime_settings import VcaRunMode, VcaRuntimeSettings


_PROCESS_TERMINATION_GRACE_SECONDS: Final = 5
_CANCELLED_BY_USER_REASON: Final = "사용자가 분석을 중지했습니다."

# run별로 실행 중인 subprocess를 추적하여, 별도의 HTTP 요청 스레드에서 온
# 취소 요청이 해당 프로세스에 신호를 보낼 수 있게 한다. 프로세스를 소유한
# 백그라운드 스레드가 실행 종료 시(성공/실패 무관) 자신의 항목을 정리한다.
_active_processes: dict[str, subprocess.Popen[str]] = {}
_active_processes_lock = threading.Lock()
_cancelled_run_ids: set[str] = set()
_cancelled_run_ids_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class VcaRunFailedError(Exception):
    assessment_id: AssessmentId
    reason: str

    def __str__(self) -> str:
        return f"VCA run failed for {self.assessment_id}: {self.reason}"


def cancel_run(run_id: str) -> bool:
    """`run_id`에 해당하는 프로세스가 살아있다면 SIGTERM 신호를 보낸다.

    SIGTERM만 보내고 바로 반환하며, 프로세스 종료를 기다리지 않는다.
    이미 `_run_command()`의 `process.communicate()`에서 대기 중이던
    백그라운드 스레드가 프로세스 종료를 감지하고
    `_run_vca_and_record_failure` 경로로 실패를 기록하므로, 프로세스의
    stdout/stderr를 읽는 지점은 그곳 한 곳뿐이다.
    """
    with _active_processes_lock:
        process = _active_processes.get(run_id)
    if process is None or process.poll() is not None:
        return False
    with _cancelled_run_ids_lock:
        _cancelled_run_ids.add(run_id)
    os.killpg(process.pid, signal.SIGTERM)
    return True


def _mark_cancellable(run_id: str, process: subprocess.Popen[str]) -> None:
    with _active_processes_lock:
        _active_processes[run_id] = process


def _forget_cancellable(run_id: str) -> None:
    with _active_processes_lock:
        _ = _active_processes.pop(run_id, None)
    with _cancelled_run_ids_lock:
        _cancelled_run_ids.discard(run_id)


def _was_cancelled(run_id: str) -> bool:
    with _cancelled_run_ids_lock:
        return run_id in _cancelled_run_ids


# CalledProcessError의 stdout/stderr를 합쳐 실패 사유를 만든다. vca_v2는
# 어느 스테이지가 왜 실패했는지를 stdout에 찍고(modules.orchestration.
# stage_progress), 실제 크래시(파이썬 트레이스백)나 uv/torch 경고는
# stderr에 남는다. 어느 한쪽만 보면 stderr의 사소한 경고 한두 줄이 stdout의
# 진짜 원인을 가리거나, 반대로 stdout만 봐서 stderr의 트레이스백을 놓칠 수
# 있으므로 둘 다 있으면 둘 다 담는다.
def _failure_reason(error: subprocess.CalledProcessError) -> str:
    stdout = error.stdout.strip()
    stderr = error.stderr.strip()
    parts = [part for part in (stdout, stderr) if part]
    return "\n".join(parts) if parts else "run exited non-zero"


# vca_v2 엔진을 subprocess로 실행할 커맨드를 구성하고 실행한 뒤, 실패
# 유형(비정상 종료/타임아웃/취소/uv 부재)을 VcaRunFailedError로 통일해
# 던진다.
def run_vca(
    run: AssessmentRun,
    input_directory: Path,
    settings: VcaRuntimeSettings,
    resume_from_stage: str | None,
) -> None:
    command = [
        "uv",
        "run",
        "python",
        "-m",
        "modules.orchestration.startup",
        str(run.project_name),
        str(input_directory),
    ]
    match settings.run_mode:
        case VcaRunMode.DRY_RUN:
            command.append("--dry-run")
        case VcaRunMode.REAL:
            if settings.device is not None:
                command.extend(("--device", str(settings.device)))
        case unexpected:
            assert_never(unexpected)
    if settings.max_images is not None:
        command.extend(("--max-images", str(settings.max_images)))
    if settings.model_cache_root is not None:
        command.extend(("--model-cache-root", str(settings.model_cache_root)))
    if settings.allow_unverified_model_hashes:
        command.append("--allow-unverified-model-hashes-local-only")
    if resume_from_stage is not None:
        command.extend(("--resume-from-stage", resume_from_stage))
    run_id = str(run.run_id)
    try:
        _run_command(
            command, cwd=settings.engine_root, timeout=settings.timeout_seconds, run_id=run_id
        )
    except subprocess.CalledProcessError as error:
        if _was_cancelled(run_id):
            raise VcaRunFailedError(run.assessment_id, _CANCELLED_BY_USER_REASON) from error
        raise VcaRunFailedError(
            run.assessment_id, _failure_reason(error)
        ) from error
    except subprocess.TimeoutExpired as error:
        raise VcaRunFailedError(
            run.assessment_id, f"run timed out after {error.timeout} seconds"
        ) from error
    except FileNotFoundError as error:
        raise VcaRunFailedError(run.assessment_id, "uv executable was not found") from error
    finally:
        _forget_cancellable(run_id)


# 실제로 subprocess를 띄우고 완료까지 대기한다. 시작하자마자
# _mark_cancellable()로 등록해 cancel_run()이 이 프로세스를 찾아
# SIGTERM을 보낼 수 있게 한다.
def _run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    run_id: str,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    _mark_cancellable(run_id, process)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        _terminate_process_group(process)
        raise subprocess.TimeoutExpired(command, timeout) from error
    if process.returncode != 0:
        raise subprocess.CalledProcessError(
            process.returncode,
            command,
            output=stdout,
            stderr=stderr,
        )
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


# 타임아웃된 프로세스 그룹에 SIGTERM을 보내고, 유예 시간 안에 종료되지
# 않으면 SIGKILL로 강제 종료한다.
def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    os.killpg(process.pid, signal.SIGTERM)
    try:
        _ = process.communicate(timeout=_PROCESS_TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        _ = process.communicate()
