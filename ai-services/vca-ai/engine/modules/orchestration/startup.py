"""Project-level startup CLI for the implemented VCA workspace stages."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

from modules.orchestration.receipts import (
    startup_payload,
    write_startup_receipt,
)
from modules.orchestration.stage_execution import (
    EXECUTED_STAGE_NAMES,
    STARTUP_FAILURE_EXIT_CODE,
    StageExecutionRequest,
    StartupStageRunners,
    execute_startup_stages,
)
from modules.orchestration.stage_paths import StagePathMap, stage_paths
from modules.shared import (
    ContractValidationError,
    PathSafetyError,
    ensure_safe_run_root,
)

IMAGE_SUFFIXES: Final = frozenset(
    {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff"}
)
RESULT_ROOT: Final = Path("output") / "result"
REAL_PREPROCESSING_DETECTOR_LANE: Final = "owlv2_sam2"


type PreprocessingRunner = Callable[[tuple[str, ...]], int]


@dataclass(frozen=True, slots=True)
class StartupRequest:
    """Parsed startup request after CLI boundary validation."""

    project_name: str
    input_image_folder: Path
    output_root: Path
    stage_paths: StagePathMap
    image_paths: tuple[Path, ...]
    preprocessing_arguments: tuple[str, ...]
    device: str
    model_cache_root: Path
    dry_run: bool
    verify_model_hashes: bool
    resume_from_stage: str | None = None


class _CliNamespace(argparse.Namespace):
    project_name: str
    input_image_folder: Path
    dry_run: bool
    device: str | None
    model_cache_root: Path | None
    max_images: str | None
    allow_unverified_model_hashes_local_only: bool
    resume_from_stage: str | None

    def __init__(self) -> None:
        """Initialize typed defaults before argparse mutates the namespace."""
        super().__init__()
        self.project_name = ""
        self.input_image_folder = Path()
        self.dry_run = False
        self.device = None
        self.model_cache_root = None
        self.max_images = None
        self.resume_from_stage = None
        self.allow_unverified_model_hashes_local_only = False


# 스타트업 CLI 인자 파서를 만든다. _request에서 사용된다.
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("project_name")
    _ = parser.add_argument("input_image_folder", type=Path)
    _ = parser.add_argument("--dry-run", action="store_true")
    _ = parser.add_argument("--device", choices=("auto", "cuda", "mps", "cpu"))
    _ = parser.add_argument("--model-cache-root", type=Path, default=None)
    _ = parser.add_argument("--max-images", default=None)
    _ = parser.add_argument(
        "--allow-unverified-model-hashes-local-only",
        action="store_true",
    )
    _ = parser.add_argument(
        "--resume-from-stage",
        choices=EXECUTED_STAGE_NAMES,
        default=None,
    )
    return parser


# CLI로 받은 project_name이 워크스페이스 로컬 단일 디렉터리 이름인지
# 검증한다(경로 탈출 방지). _request에서 호출된다.
def _project_name(raw_project_name: str) -> str:
    project_name = raw_project_name.strip()
    if not project_name:
        field = "project_name"
        reason = "must not be blank"
        raise ContractValidationError(field, reason)
    project_path = Path(project_name)
    if project_path.is_absolute() or len(project_path.parts) != 1:
        field = "project_name"
        reason = "must be a single workspace-local name"
        raise ContractValidationError(field, reason)
    if project_name in {".", ".."}:
        field = "project_name"
        reason = "must be a single workspace-local name"
        raise ContractValidationError(field, reason)
    return project_name


# 입력 이미지 폴더 경로를 워크스페이스 기준으로 절대 경로화하고 존재를
# 검증한다. _request에서 호출된다.
def _input_folder(raw_folder: Path, workspace_root: Path) -> Path:
    folder = raw_folder.expanduser()
    if not folder.is_absolute():
        folder = workspace_root / folder
    resolved = folder.resolve()
    if not resolved.is_dir():
        field = "input_image_folder"
        reason = "must be a directory"
        raise ContractValidationError(field, reason)
    return resolved


# 입력 폴더에서 지원하는 이미지 확장자 파일만 정렬해 골라낸다. 하나도 없으면
# 예외를 던진다. _request에서 호출된다.
def _image_paths(input_image_folder: Path) -> tuple[Path, ...]:
    images = tuple(
        path.resolve()
        for path in sorted(input_image_folder.iterdir())
        if path.is_file() and path.suffix.casefold() in IMAGE_SUFFIXES
    )
    if not images:
        field = "input_image_folder"
        reason = "contains no images"
        raise ContractValidationError(field, reason)
    return images


# 파싱된 스타트업 CLI 인자로부터 preprocessing 서브프로세스에 넘길 CLI 인자
# 튜플을 조립한다. _request에서 StartupRequest를 만든 뒤 호출된다.
def _preprocessing_arguments(
    parsed: _CliNamespace,
    request: StartupRequest,
) -> tuple[str, ...]:
    arguments: list[str] = [str(path) for path in request.image_paths]
    arguments.extend(
        (
            "--project-name",
            request.project_name,
            "--run-root",
            str(request.stage_paths.preprocessing),
        )
    )
    if parsed.dry_run:
        arguments.append("--dry-run")
    else:
        arguments.extend(("--detector-lane", REAL_PREPROCESSING_DETECTOR_LANE))
    if parsed.device is not None:
        arguments.extend(("--device", parsed.device))
    if parsed.model_cache_root is not None:
        arguments.extend(("--model-cache-root", str(parsed.model_cache_root)))
    if parsed.max_images is not None:
        arguments.extend(("--max-images", parsed.max_images))
    return tuple(arguments)


# CLI 인자를 파싱하고 검증해 완전한 StartupRequest를 만든다. run()에서
# 실행 전 가장 먼저 호출되는 진입점이다.
def _request(arguments: Sequence[str], workspace_root: Path) -> StartupRequest:
    parsed = _CliNamespace()
    _ = _parser().parse_args(arguments, namespace=parsed)
    project_name = _project_name(parsed.project_name)
    resolved_workspace = workspace_root.expanduser().resolve()
    input_image_folder = _input_folder(parsed.input_image_folder, resolved_workspace)
    output_root = ensure_safe_run_root(
        resolved_workspace, resolved_workspace / RESULT_ROOT / project_name
    )
    paths = stage_paths(resolved_workspace, project_name)
    image_paths = _image_paths(input_image_folder)
    device = "auto" if parsed.device is None else parsed.device
    model_cache_root = (
        resolved_workspace / "models"
        if parsed.model_cache_root is None
        else parsed.model_cache_root.expanduser()
    )
    if not model_cache_root.is_absolute():
        model_cache_root = resolved_workspace / model_cache_root
    request = StartupRequest(
        project_name,
        input_image_folder,
        output_root,
        paths,
        image_paths,
        (),
        device,
        model_cache_root,
        parsed.dry_run,
        not parsed.allow_unverified_model_hashes_local_only,
        parsed.resume_from_stage,
    )
    return replace(
        request,
        preprocessing_arguments=_preprocessing_arguments(parsed, request),
    )


# 모든 스테이지를 실행하고 startup 리시트를 기록한다. run()에서 요청 파싱이
# 성공한 뒤 호출된다.
def _execute(
    request: StartupRequest,
    stage_runners: StartupStageRunners,
) -> int:
    request.output_root.mkdir(parents=True, exist_ok=True)
    result = execute_startup_stages(
        StageExecutionRequest(
            request.project_name,
            request.stage_paths,
            request.preprocessing_arguments,
            request.device,
            request.model_cache_root,
            request.dry_run,
            request.verify_model_hashes,
            request.output_root,
            request.resume_from_stage,
        ),
        stage_runners,
    )
    write_startup_receipt(
        request,
        startup_payload(
            request,
            result.status,
            result.stages,
            result.failed_stage,
            result.final_success_evaluation,
        ),
    )
    return result.exit_code


def run(
    arguments: Sequence[str],
    *,
    workspace_root: Path | None = None,
    preprocessing_runner: PreprocessingRunner | None = None,
    stage_runners: StartupStageRunners | None = None,
) -> int:
    """Run the project startup pipeline from parsed CLI arguments."""
    root = Path.cwd() if workspace_root is None else workspace_root
    try:
        request = _request(arguments, root)
    except (ContractValidationError, PathSafetyError) as error:
        _print_startup_failure("startup request", error)
        return STARTUP_FAILURE_EXIT_CODE
    runners = _stage_runners(stage_runners, preprocessing_runner)
    try:
        return _execute(request, runners)
    except (ContractValidationError, PathSafetyError) as error:
        _print_startup_failure("startup execution", error)
        return STARTUP_FAILURE_EXIT_CODE


# 실패 사유를 stderr에 한 줄로 출력한다. run()의 두 예외 처리 경로에서
# 호출된다.
def _print_startup_failure(stage: str, error: Exception) -> None:
    print(  # noqa: T201
        f"startup: failed {stage}: {type(error).__name__}: {error}",
        file=sys.stderr,
    )


# 테스트/호출자가 넘긴 러너 오버라이드가 있으면 그것을, 없으면 기본
# StartupStageRunners를 선택한다. run()에서 호출된다.
def _stage_runners(
    stage_runners: StartupStageRunners | None,
    preprocessing_runner: PreprocessingRunner | None,
) -> StartupStageRunners:
    if stage_runners is not None:
        return stage_runners
    if preprocessing_runner is not None:
        return StartupStageRunners(preprocessing=preprocessing_runner)
    return StartupStageRunners()


# VCA_FAULTHANDLER=1이면 SIGUSR1을 받았을 때 현재 모든 스레드의 파이썬
# 스택을 stderr에 덤프하도록 등록한다. py-spy/gdb 같은 ptrace 기반 도구가
# 막힌 컨테이너(SYS_PTRACE capability 없음)에서도 외부에서 `kill -USR1 <pid>`
# 로 이 프로세스가 지금 어디서 멈춰 있는지 확인할 수 있다.
def _register_faulthandler_if_enabled() -> None:
    if not os.environ.get("VCA_FAULTHANDLER"):
        return
    import faulthandler
    import signal

    faulthandler.register(signal.SIGUSR1, file=sys.stderr, all_threads=True)


def main() -> int:
    """Entrypoint for `python -m modules.orchestration.startup`."""
    _register_faulthandler_if_enabled()
    return run(tuple(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
