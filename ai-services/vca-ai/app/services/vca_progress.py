import json
from pathlib import Path

from app.services.assessment_models import (
    AssessmentProgress,
    AssessmentRun,
    AssessmentStage,
    AssessmentStageProgress,
)
from app.services.vca_runtime_settings import VcaRuntimeSettings, runtime_settings_from_env


def get_assessment_progress(run: AssessmentRun) -> AssessmentProgress:
    """엔진이 직접 쓰는 progress.json을 읽는다. 파일이 없으면
    RUNNING/단계없음으로 간주한다.

    의도적으로 무상태(stateless)이다: 이 어댑터는 run 상태를 메모리에
    따로 들고 있지 않고, modules.orchestration.startup이 지금까지
    디스크에 쓴 내용을 그대로 반영할 뿐이다.
    """
    settings = runtime_settings_from_env()
    payload = read_progress_payload(_progress_path(run, settings))
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


# JSON 리시트 파일(progress.json/startup.json 공용)을 읽어 dict로
# 파싱한다. 파일이 아직 없거나(파이프라인 시작 전) 파싱 중이라 깨진
# 상태로 읽히면 오류 대신 None을 반환한다. vca_resume도 startup.json을
# 읽는 데 이 함수를 그대로 재사용한다.
def read_progress_payload(path: Path) -> dict[str, object] | None:
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
# "failed" 상태의 progress.json을 만들어 쓴다. assessment_runs의
# _run_vca_and_record_failure()에서만 호출된다. 임시 파일에 쓴 뒤
# rename하여 원자적으로 교체한다.
def write_adapter_failure_progress(
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
