"""Project-scoped startup stage execution."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final, NoReturn

if TYPE_CHECKING:
    from pathlib import Path

    from modules.orchestration.stage_paths import StagePathMap

from modules.orchestration import stage_progress
from modules.orchestration.receipts import (
    STAGE_NAMES,
    StageReceiptPayload,
    StartupStatus,
    stage_payload,
)
from modules.orchestration.stage_runner_contracts import (
    ProjectStageRequest,
    ProjectStageRunner,
    StartupStageRunners,
)
from modules.rag.qwen.qwen_bridge_json import parse_json_object
from modules.shared import ContractValidationError, PathSafetyError

STARTUP_FAILURE_EXIT_CODE: Final = 2
MASK_REFINING_DRY_RUN_REASON: Final = (
    "skipped during startup dry-run because mask_refining has no dry-run contract"
)
ANOMALY_GROUPING_DRY_RUN_REASON: Final = (
    "skipped during startup dry-run because anomaly_grouping requires "
    "mask_refining outputs"
)
REPORT_GENERATING_DRY_RUN_REASON: Final = (
    "skipped during startup dry-run because report_generating requires "
    "anomaly_grouping outputs"
)
POST_MASK_DRY_RUN_REASONS: Final = {
    "anomaly_grouping": ANOMALY_GROUPING_DRY_RUN_REASON,
    "report_generating": REPORT_GENERATING_DRY_RUN_REASON,
}
EXECUTED_STAGE_NAMES: Final = (
    "preprocessing",
    "rough_masking",
    "visual_cue_generation",
    "rag",
    "prompt_generating",
    "mask_refining",
    "anomaly_grouping",
    "report_generating",
)

type StageRunOutcome = tuple[StartupStatus, int | None, str | None]

__all__ = (
    "STARTUP_FAILURE_EXIT_CODE",
    "ProjectStageRequest",
    "ProjectStageRunner",
    "StageExecutionRequest",
    "StageExecutionResult",
    "StartupStageRunners",
    "execute_startup_stages",
    "mask_refining_arguments",
)


@dataclass(frozen=True, slots=True)
class StageExecutionRequest:
    """Inputs required to execute startup stages for one project."""

    project_name: str
    paths: StagePathMap
    preprocessing_arguments: tuple[str, ...]
    device: str
    model_cache_root: Path
    dry_run: bool
    verify_model_hashes: bool


@dataclass(frozen=True, slots=True)
class StageExecutionResult:
    """Receipt-ready result of a startup stage sequence."""

    status: StartupStatus
    failed_stage: str | None
    exit_code: int
    stages: list[StageReceiptPayload]


def execute_startup_stages(
    request: StageExecutionRequest,
    runners: StartupStageRunners,
) -> StageExecutionResult:
    """Run startup stages through mask refinement and stop on first failure."""
    stages: list[StageReceiptPayload] = []
    active_request = request
    for stage_name in EXECUTED_STAGE_NAMES:
        stage_progress.print_started(stage_name)
        status, exit_code, reason = _run_stage(stage_name, active_request, runners)
        stage_progress.print_outcome(stage_name, status, exit_code, reason)
        stages.append(
            stage_payload(
                stage_name,
                status,
                active_request.paths.output_dir(stage_name),
                exit_code=exit_code,
                reason=reason,
            )
        )
        match status:
            case StartupStatus.COMPLETED | StartupStatus.SKIPPED:
                if stage_name == "preprocessing" and status is StartupStatus.COMPLETED:
                    active_request = _with_preprocessing_device(active_request)
                continue
            case StartupStatus.FAILED:
                remaining_stages = _remaining_stage_payloads(
                    active_request.paths, stage_name
                )
                stages.extend(remaining_stages)
                for skipped_stage in remaining_stages:
                    stage_progress.print_outcome(
                        skipped_stage["name"],
                        StartupStatus.SKIPPED,
                        None,
                        skipped_stage.get("reason"),
                    )
                return StageExecutionResult(
                    status, stage_name, _failure_exit_code(exit_code), stages
                )
    return StageExecutionResult(StartupStatus.COMPLETED, None, 0, stages)


def _with_preprocessing_device(request: StageExecutionRequest) -> StageExecutionRequest:
    if request.dry_run:
        return request
    manifest_path = (
        request.paths.preprocessing / "manifests" / "real_preprocessing_manifest.json"
    )
    try:
        payload = parse_json_object(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return request
    device = payload.get("device")
    if not isinstance(device, str) or not device.strip():
        field = "preprocessing_manifest.device"
        reason = "must be a non-empty string"
        raise ContractValidationError(field, reason)
    return replace(request, device=device)


def mask_refining_arguments(request: StageExecutionRequest) -> tuple[str, ...]:
    """Build the mask-refining CLI argument tuple from module roots."""
    paths = request.paths
    arguments = [
        "--prompt-output-dir",
        str(paths.prompt_generating),
        "--rough-root",
        str(paths.rough_masking),
        "--asset-root",
        str(paths.preprocessing),
        "--output-dir",
        str(paths.mask_refining),
        "--model-cache-root",
        str(request.model_cache_root),
        "--device",
        request.device,
    ]
    if not request.verify_model_hashes:
        arguments.append("--no-verify-model-hashes")
    return tuple(arguments)


def _run_stage(
    stage_name: str,
    request: StageExecutionRequest,
    runners: StartupStageRunners,
) -> StageRunOutcome:
    try:
        match stage_name:
            case "preprocessing":
                outcome = _exit_outcome(
                    runners.preprocessing(request.preprocessing_arguments)
                )
            case "mask_refining":
                outcome = _run_mask_refining(request, runners)
            case (
                "rough_masking"
                | "visual_cue_generation"
                | "rag"
                | "prompt_generating"
                | "anomaly_grouping"
                | "report_generating"
            ):
                outcome = _run_project_stage(stage_name, request, runners)
            case _:
                _invalid_stage_name(stage_name)
    except (RuntimeError, OSError, ContractValidationError, PathSafetyError) as error:
        return (
            StartupStatus.FAILED,
            STARTUP_FAILURE_EXIT_CODE,
            f"{stage_name} raised {type(error).__name__}",
        )
    return outcome


def _run_mask_refining(
    request: StageExecutionRequest,
    runners: StartupStageRunners,
) -> StageRunOutcome:
    if request.dry_run:
        return _skipped_outcome(MASK_REFINING_DRY_RUN_REASON)
    return _exit_outcome(runners.mask_refining(mask_refining_arguments(request)))


def _run_project_stage(
    stage_name: str,
    request: StageExecutionRequest,
    runners: StartupStageRunners,
) -> StageRunOutcome:
    dry_run_reason = POST_MASK_DRY_RUN_REASONS.get(stage_name)
    if request.dry_run and dry_run_reason is not None:
        return _skipped_outcome(dry_run_reason)
    runner = _project_runner(stage_name, runners)
    return _exit_outcome(runner(_project_request(stage_name, request)))


def _exit_outcome(exit_code: int) -> StageRunOutcome:
    status = StartupStatus.COMPLETED if exit_code == 0 else StartupStatus.FAILED
    return status, exit_code, None


def _skipped_outcome(reason: str) -> StageRunOutcome:
    return StartupStatus.SKIPPED, None, reason


def _project_request(
    stage_name: str,
    request: StageExecutionRequest,
) -> ProjectStageRequest:
    return ProjectStageRequest(
        request.project_name,
        stage_name,
        request.paths,
        request.device,
        request.model_cache_root,
        request.dry_run,
        request.verify_model_hashes,
    )


def _failure_exit_code(exit_code: int | None) -> int:
    if exit_code is None:
        return STARTUP_FAILURE_EXIT_CODE
    return exit_code


def _project_runner(
    stage_name: str, runners: StartupStageRunners
) -> ProjectStageRunner:
    project_runners = {
        "rough_masking": runners.rough_masking,
        "visual_cue_generation": runners.visual_cue_generation,
        "rag": runners.rag,
        "prompt_generating": runners.prompt_generating,
        "anomaly_grouping": runners.anomaly_grouping,
        "report_generating": runners.report_generating,
    }
    runner = project_runners.get(stage_name)
    if runner is None:
        return _invalid_stage_name(stage_name)
    return runner


def _invalid_stage_name(stage_name: str) -> NoReturn:
    field = "stage_name"
    raise ContractValidationError(field, stage_name)


def _remaining_stage_payloads(
    paths: StagePathMap, failed_stage: str
) -> list[StageReceiptPayload]:
    failed_index = STAGE_NAMES.index(failed_stage)
    reason = f"skipped because {failed_stage} failed"
    return [
        stage_payload(
            stage_name,
            StartupStatus.SKIPPED,
            paths.output_dir(stage_name),
            reason=reason,
        )
        for stage_name in STAGE_NAMES[failed_index + 1 :]
    ]
