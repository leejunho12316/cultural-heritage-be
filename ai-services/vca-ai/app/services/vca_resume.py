import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from app.services.assessment_models import ENGINE_OUTPUT_STAGES, ProjectName
from app.services.vca_progress import read_progress_payload
from app.services.vca_runtime_settings import VcaRuntimeSettings


@dataclass(frozen=True, slots=True)
class _ResumePlan:
    resume_from_stage: str
    completed_stage_names: tuple[str, ...]


_RESUMABLE_STAGE_NAMES: Final = ENGINE_OUTPUT_STAGES[:-1]  # "result"는 스테이지가 아님


# assessment_runs.create_assessment_run()에서 resume_from_project_name이
# 있을 때만 호출된다. 이전 실패 run의 startup.json을 읽어 이어가기 계획을
# 세우고, 계획대로 산출물을 복사한 뒤 실제로 넘길 --resume-from-stage 값을
# 돌려준다. 전제가 하나라도 안 맞으면 None을 돌려줘 호출자가 조용히 전체
# 재실행으로 폴백하게 한다.
def prepare_resume(
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


# prepare_resume에서 호출된다. 이전 run의 startup.json에서 앞에서부터
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
    payload = read_progress_payload(receipt_path)
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
    return None  # 모든 스테이지가 이미 완료됨 - 실패한 것도, 재개할 것도 없음


# prepare_resume에서 호출된다. 완료된 스테이지들의 산출물 디렉터리와
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
