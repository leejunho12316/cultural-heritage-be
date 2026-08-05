import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NewType


AssessmentId = NewType("AssessmentId", str)
AssessmentRunId = NewType("AssessmentRunId", str)
InputImageFolder = NewType("InputImageFolder", str)
ProjectName = NewType("ProjectName", str)

_RUN_PREFIX: Final = "vca-"
_ASSESSMENT_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_PROJECT_NAME_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_SUPPORTED_IMAGE_SUFFIXES: Final = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
)
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


@dataclass(frozen=True, slots=True)
class AssessmentRun:
    run_id: AssessmentRunId
    assessment_id: AssessmentId


@dataclass(frozen=True, slots=True)
class AssessmentFinding:
    category: str
    severity: str
    message: str


@dataclass(frozen=True, slots=True)
class AssessmentReport:
    run: AssessmentRun
    summary: str
    findings: tuple[AssessmentFinding, ...]


@dataclass(frozen=True, slots=True)
class InvalidAssessmentRunIdError(Exception):
    run_id: str

    def __str__(self) -> str:
        return f"Unknown VCA assessment run: {self.run_id}"


@dataclass(frozen=True, slots=True)
class InvalidProjectNameError(Exception):
    project_name: str

    def __str__(self) -> str:
        return f"Invalid VCA project name: {self.project_name}"


@dataclass(frozen=True, slots=True)
class InvalidInputImageFolderError(Exception):
    input_image_folder: str

    reason: str

    def __str__(self) -> str:
        return f"Invalid VCA input image folder: {self.reason}"


@dataclass(frozen=True, slots=True)
class VcaDryRunFailedError(Exception):
    assessment_id: AssessmentId
    reason: str

    def __str__(self) -> str:
        return f"VCA dry-run failed for {self.assessment_id}: {self.reason}"


@dataclass(frozen=True, slots=True)
class VcaRuntimeSettings:
    shared_storage_root: Path
    engine_root: Path
    timeout_seconds: int


def create_assessment_run(
    assessment_id: AssessmentId,
    project_name: ProjectName,
    input_image_folder: InputImageFolder,
) -> AssessmentRun:
    settings = runtime_settings_from_env()
    _validate_project_name(project_name)
    input_directory = _validate_input_image_folder(input_image_folder, settings)
    _clear_project_output(assessment_id, project_name, settings)
    _run_vca_dry_run(assessment_id, project_name, input_directory, settings)
    return AssessmentRun(
        run_id=AssessmentRunId(f"{_RUN_PREFIX}{assessment_id}"),
        assessment_id=assessment_id,
    )


def runtime_settings_from_env() -> VcaRuntimeSettings:
    return VcaRuntimeSettings(
        shared_storage_root=Path(
            os.environ.get("VCA_SHARED_STORAGE_ROOT", "/shared/vca")
        ),
        engine_root=Path(os.environ.get("VCA_ENGINE_ROOT", "/vca_v2")),
        timeout_seconds=int(os.environ.get("VCA_DRY_RUN_TIMEOUT_SECONDS", "120")),
    )


def get_assessment_run(run_id: str) -> AssessmentRun:
    if not run_id.startswith(_RUN_PREFIX):
        raise InvalidAssessmentRunIdError(run_id)

    assessment_id = run_id.removeprefix(_RUN_PREFIX)
    if _ASSESSMENT_ID_PATTERN.fullmatch(assessment_id) is None:
        raise InvalidAssessmentRunIdError(run_id)

    return AssessmentRun(
        run_id=AssessmentRunId(run_id),
        assessment_id=AssessmentId(assessment_id),
    )


def get_assessment_report(run: AssessmentRun) -> AssessmentReport:
    return AssessmentReport(
        run=run,
        summary="Deterministic VCA assessment placeholder.",
        findings=(
            AssessmentFinding(
                category="PLACEHOLDER",
                severity="INFO",
                message="VCA dry-run completed.",
            ),
        ),
    )


def _validate_project_name(project_name: ProjectName) -> None:
    if _PROJECT_NAME_PATTERN.fullmatch(project_name) is None:
        raise InvalidProjectNameError(str(project_name))


def _validate_input_image_folder(
    input_image_folder: InputImageFolder,
    settings: VcaRuntimeSettings,
) -> Path:
    shared_root = settings.shared_storage_root.resolve()
    input_directory = Path(input_image_folder).resolve()
    if not input_directory.is_relative_to(shared_root):
        raise InvalidInputImageFolderError(
            str(input_image_folder),
            "folder must be under VCA_SHARED_STORAGE_ROOT",
        )
    if not input_directory.is_dir():
        raise InvalidInputImageFolderError(
            str(input_image_folder),
            "folder does not exist",
        )
    if not _contains_supported_image(input_directory):
        raise InvalidInputImageFolderError(
            str(input_image_folder),
            "folder contains no supported images",
        )
    return input_directory


def _contains_supported_image(input_directory: Path) -> bool:
    return any(
        child.is_file() and child.suffix.lower() in _SUPPORTED_IMAGE_SUFFIXES
        for child in input_directory.iterdir()
    )


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
            raise VcaDryRunFailedError(
                assessment_id,
                f"failed to clear previous output for {stage}",
            ) from error


def _run_vca_dry_run(
    assessment_id: AssessmentId,
    project_name: ProjectName,
    input_directory: Path,
    settings: VcaRuntimeSettings,
) -> None:
    command = [
        "uv",
        "run",
        "python",
        "-m",
        "modules.orchestration.startup",
        str(project_name),
        str(input_directory),
        "--dry-run",
    ]
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
        raise VcaDryRunFailedError(
            assessment_id,
            error.stderr.strip() or error.stdout.strip() or "dry-run exited non-zero",
        ) from error
    except subprocess.TimeoutExpired as error:
        raise VcaDryRunFailedError(
            assessment_id,
            f"dry-run timed out after {error.timeout} seconds",
        ) from error
    except FileNotFoundError as error:
        raise VcaDryRunFailedError(
            assessment_id,
            "uv executable was not found",
        ) from error
