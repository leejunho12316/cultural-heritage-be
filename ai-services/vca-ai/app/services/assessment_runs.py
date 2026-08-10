import json
import os
import re
import signal
import shutil
import subprocess
import threading
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, assert_never

from app.services.assessment_models import (
    AssessmentFinding,
    AssessmentFindingBbox,
    AssessmentFindingCitation,
    AssessmentId,
    AssessmentProgress,
    AssessmentReport,
    AssessmentRun,
    AssessmentRunId,
    AssessmentStage,
    AssessmentStageProgress,
    InputImageFolder,
    MaxImages,
    ProjectName,
    RunTimeoutSeconds,
)
from app.services.assessment_input_validation import (
    is_valid_project_name,
    validate_input_image_folder,
    validate_project_name,
)
from app.services.vca_artifacts import load_vca_report


_RUN_PREFIX: Final = "vca-"
_RUN_PROJECT_SEPARATOR: Final = "~"
_ASSESSMENT_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_PROCESS_TERMINATION_GRACE_SECONDS: Final = 5
_CANCELLED_BY_USER_REASON: Final = "사용자가 분석을 중지했습니다."

# run별로 실행 중인 subprocess를 추적하여, 별도의 HTTP 요청 스레드에서 온
# 취소 요청이 해당 프로세스에 신호를 보낼 수 있게 한다. 프로세스를 소유한
# 백그라운드 스레드가 실행 종료 시(성공/실패 무관) 자신의 항목을 정리한다.
_active_processes: dict[str, subprocess.Popen[str]] = {}
_active_processes_lock = threading.Lock()
_cancelled_run_ids: set[str] = set()
_cancelled_run_ids_lock = threading.Lock()
_ENGINE_OUTPUT_STAGES: Final = (
    "preprocessing",
    "rough_masking",
    "visual_cue_generation",
    "rag",
    "prompt_generating",
    "mask_refining",
    "anomaly_grouping",
    "report_generating",
    "result",
)


class VcaDevice(StrEnum):
    AUTO = "auto"
    CUDA = "cuda"
    MPS = "mps"
    CPU = "cpu"


class VcaRunMode(StrEnum):
    REAL = "real"
    DRY_RUN = "dry-run"


@dataclass(frozen=True, slots=True)
class InvalidAssessmentRunIdError(Exception):
    run_id: str

    def __str__(self) -> str:
        return f"Unknown VCA assessment run: {self.run_id}"


@dataclass(frozen=True, slots=True)
class VcaRunFailedError(Exception):
    assessment_id: AssessmentId
    reason: str

    def __str__(self) -> str:
        return f"VCA run failed for {self.assessment_id}: {self.reason}"


@dataclass(frozen=True, slots=True)
class VcaRuntimeSettingsError(Exception):
    reason: str

    def __str__(self) -> str:
        return f"Invalid VCA runtime settings: {self.reason}"


@dataclass(frozen=True, slots=True)
class VcaRuntimeSettings:
    shared_storage_root: Path
    engine_root: Path
    timeout_seconds: RunTimeoutSeconds
    run_mode: VcaRunMode
    device: VcaDevice | None
    max_images: MaxImages | None
    model_cache_root: Path | None
    allow_unverified_model_hashes: bool


def create_assessment_run(
    assessment_id: AssessmentId,
    project_name: ProjectName,
    input_image_folder: InputImageFolder,
    *,
    resume_from_project_name: ProjectName | None = None,
) -> AssessmentRun:
    """run을 등록하고 파이프라인을 백그라운드로 실행한다.

    subprocess가 시작되는 즉시 반환하며, 호출자는 수십 분 걸리는 실제
    실행이 끝날 때까지 기다리지 않고 get_assessment_progress()로 단계별
    상태를 폴링한다.

    resume_from_project_name은 Spring이 같은 artifact의 가장 최근 FAILED
    run(이미지 구성이 이번 run과 정확히 같음을 이미 확인한 뒤)을 넘겨줄 때만
    채워진다. 그 run의 완료된 스테이지 산출물을 이 run의 project_name
    아래로 미리 복사해두고 vca_v2를 --resume-from-stage와 함께 실행해
    이미 끝난 스테이지를 다시 돌리지 않는다. 전제가 하나라도 안 맞으면
    (리시트 없음/파싱 실패/산출물 유실/복사 실패 등) 조용히 평소처럼
    처음부터 전체 실행으로 폴백한다 - 이어가기는 순수 최적화일 뿐이다.
    """
    settings = runtime_settings_from_env()
    validate_project_name(project_name)
    input_directory = validate_input_image_folder(
        input_image_folder, settings.shared_storage_root
    )
    run = AssessmentRun(
        run_id=AssessmentRunId(
            f"{_RUN_PREFIX}{assessment_id}{_RUN_PROJECT_SEPARATOR}{project_name}"
        ),
        assessment_id=assessment_id,
        project_name=project_name,
    )
    resume_from_stage = _prepare_resume(
        project_name, resume_from_project_name, settings
    )
    if resume_from_stage is None:
        _clear_project_output(assessment_id, project_name, settings)
    _launch_background(run, input_directory, settings, resume_from_stage)
    return run


def _launch_background(
    run: AssessmentRun,
    input_directory: Path,
    settings: VcaRuntimeSettings,
    resume_from_stage: str | None,
) -> None:
    """실제 파이프라인 작업을 요청 스레드가 아닌 별도 스레드에서 시작한다.

    테스트에서 실제 백그라운드 스레드와의 경합 없이 동기적으로(inline)
    실행되도록 monkeypatch할 수 있는 지점으로 분리해 두었다.
    """
    thread = threading.Thread(
        target=_run_vca_and_record_failure,
        args=(run, input_directory, settings, resume_from_stage),
        daemon=True,
    )
    thread.start()


# 파이프라인을 실행하고, 실패 시 어댑터 차원에서 진행 상태를 기록한다.
# _launch_background()가 생성한 백그라운드 스레드의 진입점이다.
def _run_vca_and_record_failure(
    run: AssessmentRun,
    input_directory: Path,
    settings: VcaRuntimeSettings,
    resume_from_stage: str | None,
) -> None:
    try:
        _run_vca(run, input_directory, settings, resume_from_stage)
    except (VcaRunFailedError, VcaRuntimeSettingsError) as error:
        # 파이프라인이 자체 progress.json을 쓸 만큼도 진행되지 못했거나
        # (또는 `uv` 실행 파일 부재처럼 파이프라인 외부 원인으로) 실패한
        # 경우이므로, 폴러가 "running" 상태로 계속 남아있지 않도록 여기서
        # 직접 실패를 기록한다.
        _write_adapter_failure_progress(run, settings, str(error))


# 환경변수들로부터 VcaRuntimeSettings를 조립한다. create_assessment_run(),
# get_assessment_progress(), get_assessment_report() 등 실행 설정이
# 필요한 곳에서 매 호출마다 새로 읽는다.
def runtime_settings_from_env() -> VcaRuntimeSettings:
    return VcaRuntimeSettings(
        shared_storage_root=Path(os.environ.get("VCA_SHARED_STORAGE_ROOT", "/shared/vca")),
        engine_root=Path(os.environ.get("VCA_ENGINE_ROOT", "/vca_v2")),
        timeout_seconds=_run_timeout_seconds_from_env(),
        run_mode=_run_mode_from_env(),
        device=_device_from_env(),
        max_images=_max_images_from_env(),
        model_cache_root=_model_cache_root_from_env(),
        allow_unverified_model_hashes=_allow_unverified_model_hashes_from_env(),
    )


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


# run_id 문자열("vca-{assessmentId}~{projectName}")을 분해해 AssessmentRun을
# 복원한다. 형식이 어긋나면 InvalidAssessmentRunIdError를 던진다.
def get_assessment_run(run_id: str) -> AssessmentRun:
    run_values = run_id.removeprefix(_RUN_PREFIX)
    assessment_id, separator, project_name = run_values.partition(_RUN_PROJECT_SEPARATOR)
    if (
        run_values == run_id
        or not separator
        or _ASSESSMENT_ID_PATTERN.fullmatch(assessment_id) is None
        or not is_valid_project_name(project_name)
    ):
        raise InvalidAssessmentRunIdError(run_id)
    return AssessmentRun(
        run_id=AssessmentRunId(run_id),
        assessment_id=AssessmentId(assessment_id),
        project_name=ProjectName(project_name),
    )


def get_assessment_progress(run: AssessmentRun) -> AssessmentProgress:
    """엔진이 직접 쓰는 progress.json을 읽는다. 파일이 없으면
    RUNNING/단계없음으로 간주한다.

    의도적으로 무상태(stateless)이다: 이 어댑터는 run 상태를 메모리에
    따로 들고 있지 않고, modules.orchestration.startup이 지금까지
    디스크에 쓴 내용을 그대로 반영할 뿐이다.
    """
    settings = runtime_settings_from_env()
    payload = _read_progress_payload(_progress_path(run, settings))
    if payload is None:
        return AssessmentProgress(status="running", current_stage=None, stages=())
    return AssessmentProgress(
        status=_string_field(payload.get("status"), "running"),
        current_stage=_optional_string_field(payload.get("current_stage")),
        stages=_stages_from_payload(payload.get("stages")),
        failure_reason=_optional_string_field(payload.get("adapter_failure_reason")),
        current_stage_progress=_stage_progress_from_payload(
            payload.get("current_stage_progress")
        ),
    )


def _progress_path(run: AssessmentRun, settings: VcaRuntimeSettings) -> Path:
    return (
        settings.engine_root
        / "output"
        / "result"
        / str(run.project_name)
        / "receipts"
        / "progress.json"
    )


# progress.json을 읽어 dict로 파싱한다. 파일이 아직 없거나(파이프라인
# 시작 전) 파싱 중이라 깨진 상태로 읽히면 오류 대신 None을 반환한다.
def _read_progress_payload(path: Path) -> dict[str, object] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


# progress.json의 stages 목록을 AssessmentStage로 변환한다. 예상한 형태가
# 아닌 항목/필드는 조용히 건너뛰어 방어적으로 파싱한다.
def _stages_from_payload(value: object) -> tuple[AssessmentStage, ...]:
    if not isinstance(value, list):
        return ()
    stages: list[AssessmentStage] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        status = item.get("status")
        if not isinstance(name, str) or not isinstance(status, str):
            continue
        exit_code = item.get("exit_code")
        reason = item.get("reason")
        stages.append(
            AssessmentStage(
                name=name,
                status=status,
                exit_code=exit_code if isinstance(exit_code, int) else None,
                reason=reason if isinstance(reason, str) else None,
            )
        )
    return tuple(stages)


# progress.json의 current_stage_progress를 AssessmentStageProgress로
# 변환한다. 값이 없거나(계측 안 되는 스테이지) 예상한 형태가 아니면 None을
# 돌려준다 - FE가 이 필드로 현재 스테이지의 0~100%를 그린다.
def _stage_progress_from_payload(value: object) -> AssessmentStageProgress | None:
    if not isinstance(value, dict):
        return None
    completed = value.get("completed")
    total = value.get("total")
    if not isinstance(completed, int) or not isinstance(total, int):
        return None
    return AssessmentStageProgress(completed=completed, total=total)


def _string_field(value: object, default: str) -> str:
    return value if isinstance(value, str) else default


def _optional_string_field(value: object) -> str | None:
    return value if isinstance(value, str) else None


# 파이프라인이 자체 progress.json을 쓰기 전에 실패했을 때, 어댑터가 대신
# "failed" 상태의 progress.json을 만들어 쓴다. _run_vca_and_record_failure()
# 에서만 호출된다. 임시 파일에 쓴 뒤 rename하여 원자적으로 교체한다.
def _write_adapter_failure_progress(
    run: AssessmentRun, settings: VcaRuntimeSettings, reason: str
) -> None:
    path = _progress_path(run, settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "vca-startup-progress-v1",
        "project_name": str(run.project_name),
        "status": "failed",
        "current_stage": None,
        "stages": [],
        "adapter_failure_reason": reason,
    }
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    _ = temporary.write_text(json.dumps(payload), encoding="utf-8")
    _ = temporary.replace(path)


# 엔진 산출물을 읽어(load_vca_report) 서비스 계층의 AssessmentReport로
# 변환한다. 라우터의 get_run_report()가 호출한다.
def get_assessment_report(run: AssessmentRun) -> AssessmentReport:
    settings = runtime_settings_from_env()
    artifacts = load_vca_report(
        settings.engine_root,
        str(run.project_name),
        is_dry_run=settings.run_mode is VcaRunMode.DRY_RUN,
    )
    return AssessmentReport(
        run=run,
        summary=artifacts.summary,
        findings=tuple(
            AssessmentFinding(
                category=finding.category,
                severity=finding.severity,
                message=finding.message,
                candidate_id=finding.candidate_id,
                image_id=finding.image_id,
                concept_family=finding.concept_family,
                descriptor=finding.descriptor,
                citations=tuple(
                    AssessmentFindingCitation(
                        citation_id=citation.citation_id,
                        source_citation=citation.source_citation,
                        page_number=citation.page_number,
                    )
                    for citation in finding.citations
                ),
                bbox=None
                if finding.bbox is None
                else AssessmentFindingBbox(
                    x_min=finding.bbox.x_min,
                    y_min=finding.bbox.y_min,
                    x_max=finding.bbox.x_max,
                    y_max=finding.bbox.y_max,
                ),
                polygons=finding.polygons,
            )
            for finding in artifacts.findings
        ),
        rag=artifacts.rag,
    )


# VCA_RUN_TIMEOUT_SECONDS 환경변수를 읽어 파이프라인 실행 제한시간을
# 정한다. runtime_settings_from_env()에서 호출된다.
def _run_timeout_seconds_from_env() -> RunTimeoutSeconds:
    # VCA_DRY_RUN_TIMEOUT_SECONDS는 예전 변수명으로, 아직 이름을 바꾸지
    # 않은 배포 환경도 타임아웃 값을 받을 수 있도록 fallback으로 남겨둔다.
    raw_timeout = os.environ.get(
        "VCA_RUN_TIMEOUT_SECONDS",
        os.environ.get("VCA_DRY_RUN_TIMEOUT_SECONDS", "120"),
    )
    try:
        timeout_seconds = int(raw_timeout)
    except ValueError as error:
        raise VcaRuntimeSettingsError("VCA_RUN_TIMEOUT_SECONDS must be an integer") from error
    if timeout_seconds < 1:
        raise VcaRuntimeSettingsError("VCA_RUN_TIMEOUT_SECONDS must be positive")
    return RunTimeoutSeconds(timeout_seconds)


# VCA_RUN_MODE 환경변수를 real/dry-run 중 하나로 해석한다.
def _run_mode_from_env() -> VcaRunMode:
    raw_run_mode = os.environ.get("VCA_RUN_MODE", VcaRunMode.REAL)
    try:
        return VcaRunMode(raw_run_mode)
    except ValueError as error:
        raise VcaRuntimeSettingsError(
            "VCA_RUN_MODE must be real or dry-run"
        ) from error


# VCA_DEVICE 환경변수를 읽어 추론 디바이스를 정한다. 지정하지 않으면
# None을 반환해 엔진이 auto로 판단하게 둔다.
def _device_from_env() -> VcaDevice | None:
    raw_device = os.environ.get("VCA_DEVICE")
    if raw_device is None:
        return None
    try:
        return VcaDevice(raw_device)
    except ValueError as error:
        raise VcaRuntimeSettingsError("VCA_DEVICE must be auto, cuda, mps, or cpu") from error


# VCA_MODEL_CACHE_ROOT 환경변수를 모델 캐시 경로로 해석한다.
def _model_cache_root_from_env() -> Path | None:
    raw_model_cache_root = os.environ.get("VCA_MODEL_CACHE_ROOT")
    if raw_model_cache_root is None:
        return None
    if not raw_model_cache_root.strip():
        raise VcaRuntimeSettingsError("VCA_MODEL_CACHE_ROOT must not be blank")
    return Path(raw_model_cache_root)


# VCA_LOCAL_ALLOW_UNVERIFIED_MODEL_HASHES 환경변수 값이다. 로컬 개발용
# 우회 옵션이므로 기본값은 항상 false다.
def _allow_unverified_model_hashes_from_env() -> bool:
    return os.environ.get("VCA_LOCAL_ALLOW_UNVERIFIED_MODEL_HASHES") == "true"


# VCA_MAX_IMAGES 환경변수를 읽는다. "all" 또는 양의 정수 문자열만
# 허용한다.
def _max_images_from_env() -> MaxImages | None:
    raw_max_images = os.environ.get("VCA_MAX_IMAGES")
    if raw_max_images is None:
        return None
    if raw_max_images == "all" or (
        raw_max_images.isdecimal() and int(raw_max_images) > 0
    ):
        return MaxImages(raw_max_images)
    raise VcaRuntimeSettingsError("VCA_MAX_IMAGES must be all or a positive integer")


@dataclass(frozen=True, slots=True)
class _ResumePlan:
    resume_from_stage: str
    completed_stage_names: tuple[str, ...]


_RESUMABLE_STAGE_NAMES: Final = _ENGINE_OUTPUT_STAGES[:-1]  # "result"는 스테이지가 아님


# create_assessment_run()에서 resume_from_project_name이 있을 때만 호출된다.
# 이전 실패 run의 startup.json을 읽어 이어가기 계획을 세우고, 계획대로
# 산출물을 복사한 뒤 실제로 넘길 --resume-from-stage 값을 돌려준다. 전제가
# 하나라도 안 맞으면 None을 돌려줘 호출자가 조용히 전체 재실행으로
# 폴백하게 한다.
def _prepare_resume(
    project_name: ProjectName,
    resume_from_project_name: ProjectName | None,
    settings: VcaRuntimeSettings,
) -> str | None:
    if resume_from_project_name is None:
        return None
    plan = _resume_plan(resume_from_project_name, settings)
    if plan is None:
        return None
    if not _seed_resume_output(project_name, resume_from_project_name, plan, settings):
        return None
    return plan.resume_from_stage


# _prepare_resume에서 호출된다. 이전 run의 startup.json에서 앞에서부터
# 연속으로 completed인 스테이지들을 찾고, 그 산출물 디렉터리가 실제로
# 디스크에 있는지까지 확인한다. 어느 하나라도 어긋나면(리시트 없음/파싱
# 실패/처음부터 실패/산출물 유실/전 스테이지 완료돼 이어갈 게 없음) None.
def _resume_plan(
    resume_from_project_name: ProjectName, settings: VcaRuntimeSettings
) -> _ResumePlan | None:
    receipt_path = (
        settings.engine_root
        / "output"
        / "result"
        / str(resume_from_project_name)
        / "receipts"
        / "startup.json"
    )
    payload = _read_progress_payload(receipt_path)
    if payload is None:
        return None
    raw_stages = payload.get("stages")
    if not isinstance(raw_stages, list):
        return None
    status_by_name: dict[str, str] = {}
    for item in raw_stages:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        status = item.get("status")
        if isinstance(name, str) and isinstance(status, str):
            status_by_name[name] = status
    output_root = settings.engine_root / "output"
    completed: list[str] = []
    for stage_name in _RESUMABLE_STAGE_NAMES:
        if status_by_name.get(stage_name) != "completed":
            if not completed:
                return None
            if not all(
                (output_root / name / str(resume_from_project_name)).is_dir()
                for name in completed
            ):
                return None
            return _ResumePlan(
                resume_from_stage=stage_name, completed_stage_names=tuple(completed)
            )
        completed.append(stage_name)
    return None  # every stage already completed - nothing failed, nothing to resume


# _prepare_resume에서 호출된다. 완료된 스테이지들의 산출물 디렉터리와
# startup.json이 담긴 receipts 디렉터리를 이전 run의 project_name에서 이번
# run의 project_name으로 복사한다. 복사 도중 하나라도 실패하면 절반만
# 복사된 상태가 이후 정상 실행과 섞이지 않도록 지운 뒤 실패를 알린다.
def _seed_resume_output(
    project_name: ProjectName,
    resume_from_project_name: ProjectName,
    plan: _ResumePlan,
    settings: VcaRuntimeSettings,
) -> bool:
    output_root = settings.engine_root / "output"
    copied_destinations: list[Path] = []
    try:
        for stage_name in plan.completed_stage_names:
            source = output_root / stage_name / str(resume_from_project_name)
            destination = output_root / stage_name / str(project_name)
            shutil.copytree(source, destination)
            copied_destinations.append(destination)
        receipts_source = (
            output_root / "result" / str(resume_from_project_name) / "receipts"
        )
        receipts_destination = output_root / "result" / str(project_name) / "receipts"
        receipts_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(receipts_source, receipts_destination)
        copied_destinations.append(receipts_destination)
    except OSError:
        for destination in copied_destinations:
            shutil.rmtree(destination, ignore_errors=True)
        return False
    return True


# 재실행 전에 이전 run이 각 스테이지에 남긴 산출물 디렉터리를 삭제한다.
# create_assessment_run()에서 파이프라인을 다시 시작하기 직전에 호출된다.
def _clear_project_output(
    assessment_id: AssessmentId,
    project_name: ProjectName,
    settings: VcaRuntimeSettings,
) -> None:
    output_root = settings.engine_root / "output"
    for stage in _ENGINE_OUTPUT_STAGES:
        stage_project_directory = output_root / stage / str(project_name)
        if not stage_project_directory.exists():
            continue
        try:
            shutil.rmtree(stage_project_directory)
        except OSError as error:
            raise VcaRunFailedError(
                assessment_id, f"failed to clear previous output for {stage}"
            ) from error


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
def _run_vca(
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
