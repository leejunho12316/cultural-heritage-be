import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, assert_never

from app.services.assessment_models import (
    AssessmentFinding,
    AssessmentId,
    AssessmentReport,
    AssessmentRun,
    AssessmentRunId,
    InputImageFolder,
    MaxImages,
    ProjectName,
    RunTimeoutSeconds,
)
from app.services.assessment_input_validation import (
    InvalidInputImageFolderError,
    InvalidProjectNameError,
    is_valid_project_name,
    validate_input_image_folder,
    validate_project_name,
)
from app.services.vca_artifacts import load_vca_report


__all__ = (
    "InvalidInputImageFolderError",
    "InvalidProjectNameError",
)


_RUN_PREFIX: Final = "vca-"
_RUN_PROJECT_SEPARATOR: Final = "~"
_ASSESSMENT_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
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
) -> AssessmentRun:
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
    _clear_project_output(assessment_id, project_name, settings)
    _run_vca(run, input_directory, settings)
    return run


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
            )
            for finding in artifacts.findings
        ),
    )


def _run_timeout_seconds_from_env() -> RunTimeoutSeconds:
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


def _run_mode_from_env() -> VcaRunMode:
    raw_run_mode = os.environ.get("VCA_RUN_MODE", VcaRunMode.REAL)
    try:
        return VcaRunMode(raw_run_mode)
    except ValueError as error:
        raise VcaRuntimeSettingsError(
            "VCA_RUN_MODE must be real or dry-run"
        ) from error


def _device_from_env() -> VcaDevice | None:
    raw_device = os.environ.get("VCA_DEVICE")
    if raw_device is None:
        return None
    try:
        return VcaDevice(raw_device)
    except ValueError as error:
        raise VcaRuntimeSettingsError("VCA_DEVICE must be auto, cuda, mps, or cpu") from error


def _model_cache_root_from_env() -> Path | None:
    raw_model_cache_root = os.environ.get("VCA_MODEL_CACHE_ROOT")
    if raw_model_cache_root is None:
        return None
    if not raw_model_cache_root.strip():
        raise VcaRuntimeSettingsError("VCA_MODEL_CACHE_ROOT must not be blank")
    return Path(raw_model_cache_root)


def _allow_unverified_model_hashes_from_env() -> bool:
    return os.environ.get("VCA_LOCAL_ALLOW_UNVERIFIED_MODEL_HASHES") == "true"


def _max_images_from_env() -> MaxImages | None:
    raw_max_images = os.environ.get("VCA_MAX_IMAGES")
    if raw_max_images is None:
        return None
    if raw_max_images == "all" or (
        raw_max_images.isdecimal() and int(raw_max_images) > 0
    ):
        return MaxImages(raw_max_images)
    raise VcaRuntimeSettingsError("VCA_MAX_IMAGES must be all or a positive integer")


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


def _run_vca(
    run: AssessmentRun,
    input_directory: Path,
    settings: VcaRuntimeSettings,
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
    try:
        subprocess.run(
            command,
            cwd=settings.engine_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=settings.timeout_seconds,
        )
    except subprocess.CalledProcessError as error:
        raise VcaRunFailedError(
            run.assessment_id,
            error.stderr.strip() or error.stdout.strip() or "run exited non-zero",
        ) from error
    except subprocess.TimeoutExpired as error:
        raise VcaRunFailedError(
            run.assessment_id, f"run timed out after {error.timeout} seconds"
        ) from error
    except FileNotFoundError as error:
        raise VcaRunFailedError(run.assessment_id, "uv executable was not found") from error
