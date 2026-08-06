"""CLI request parsing for safe preprocessing inputs."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from modules.shared import (
    ACTIVE_DETECTOR_LANES,
    ContractValidationError,
    DetectorLane,
    parse_detector_lane,
)


class AssetPolicy(StrEnum):
    """Public input asset policy; CLI currently supports copying only."""

    COPY = "copy"


class Device(StrEnum):
    """Allowed detector and Qwen device requests."""

    AUTO = "auto"
    MPS = "mps"
    CUDA = "cuda"
    CPU = "cpu"


type Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class RunRequest:
    """Parsed CLI inputs before preflight validates filesystem state."""

    workspace_root: Path
    run_root: Path
    image_paths: tuple[Path, ...]
    device: Device
    qwen_device: Device
    prompt_pack: str
    dry_run: bool
    limit: int | None
    force_rerun: bool
    emit_pre_qwen_preview: bool
    detector_lanes: tuple[DetectorLane, ...]
    followup_request_path: Path | None
    project_name: str | None = None
    asset_policy: AssetPolicy = AssetPolicy.COPY


def _device(raw_value: str, field: str) -> Device:
    try:
        return Device(raw_value)
    except ValueError as error:
        raise ContractValidationError(field, raw_value) from error


def _option_value(arguments: Sequence[str], index: int, option: str) -> tuple[str, int]:
    next_index = index + 1
    if next_index >= len(arguments) or arguments[next_index].startswith("--"):
        raise ContractValidationError(option, "requires a value")
    return arguments[next_index], next_index


def _invalid(field: str, reason: str) -> ContractValidationError:
    return ContractValidationError(field, reason)


def _resolve_from_workspace(workspace_root: Path, raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    return candidate.resolve()


def _default_run_root(workspace_root: Path, clock: Clock) -> Path:
    timestamp = clock().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    return workspace_root / "output" / "preprocessing" / timestamp


def _project_name(raw_project_name: str | None) -> str | None:
    if raw_project_name is None:
        return None
    project_name = raw_project_name.strip()
    if not project_name:
        field = "project_name"
        reason = "must not be blank"
        raise _invalid(field, reason)
    project_path = Path(project_name)
    if project_path.is_absolute() or len(project_path.parts) != 1:
        field = "project_name"
        reason = "must be a workspace-local directory name"
        raise _invalid(field, reason)
    return project_name


def _project_images(workspace_root: Path, project_name: str | None) -> tuple[Path, ...]:
    if project_name is None:
        return ()
    project_root = workspace_root / project_name
    if not project_root.is_dir():
        field = "project_name"
        reason = "must reference an existing workspace directory"
        raise _invalid(field, reason)
    return tuple(
        path.resolve() for path in sorted(project_root.iterdir()) if path.is_file()
    )


def _split_arguments(
    arguments: Sequence[str],
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...], tuple[str, ...]]:
    value_options = {
        "--run-root",
        "--device",
        "--qwen-device",
        "--prompt-pack",
        "--limit",
        "--detector-lane",
        "--followup-request",
        "--project-name",
    }
    flags = {"--dry-run", "--force-rerun", "--emit-pre-qwen-preview"}
    selected_flags: list[str] = []
    selected_options: list[tuple[str, str]] = []
    image_paths: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in value_options:
            value, index = _option_value(arguments, index, argument)
            selected_options.append((argument, value))
        elif argument in flags:
            selected_flags.append(argument)
        elif argument.startswith("--"):
            field = "argument"
            raise _invalid(field, f"unsupported option {argument}")
        else:
            image_paths.append(argument)
        index += 1
    return tuple(selected_flags), tuple(selected_options), tuple(image_paths)


def _last_option(options: tuple[tuple[str, str], ...], name: str) -> str | None:
    values = tuple(value for option, value in options if option == name)
    return values[-1] if values else None


def _parse_limit(options: tuple[tuple[str, str], ...]) -> int | None:
    raw_limit = _last_option(options, "--limit")
    if raw_limit is None:
        return None
    try:
        limit = int(raw_limit)
    except ValueError as error:
        field = "limit"
        raise _invalid(field, raw_limit) from error
    if limit < 0:
        field = "limit"
        reason = "must be non-negative"
        raise _invalid(field, reason)
    return limit


def parse_run_request(
    arguments: Sequence[str],
    *,
    workspace_root: Path,
    clock: Clock = datetime.now,
) -> RunRequest:
    """Parse positional images and supported T3 CLI options without I/O writes."""
    resolved_workspace = workspace_root.expanduser().resolve()
    flags, options, raw_images = _split_arguments(arguments)
    raw_run_root = _last_option(options, "--run-root")
    project_name = _project_name(_last_option(options, "--project-name"))
    prompt_pack = _last_option(options, "--prompt-pack") or "current-default"
    if not prompt_pack.strip():
        field = "prompt_pack"
        reason = "must not be blank"
        raise _invalid(field, reason)
    detector_lanes = tuple(
        parse_detector_lane(value)
        for option, value in options
        if option == "--detector-lane"
    )
    raw_followup = _last_option(options, "--followup-request")
    if raw_run_root is not None:
        run_root = _resolve_from_workspace(resolved_workspace, raw_run_root)
    elif project_name is not None:
        run_root = resolved_workspace / "output" / "preprocessing" / project_name
    else:
        run_root = _default_run_root(resolved_workspace, clock)
    return RunRequest(
        workspace_root=resolved_workspace,
        run_root=run_root,
        image_paths=tuple(
            _resolve_from_workspace(resolved_workspace, image_path)
            for image_path in raw_images
        )
        or _project_images(resolved_workspace, project_name),
        device=_device(_last_option(options, "--device") or Device.AUTO, "device"),
        qwen_device=_device(
            _last_option(options, "--qwen-device") or Device.AUTO,
            "qwen_device",
        ),
        prompt_pack=prompt_pack,
        dry_run="--dry-run" in flags,
        limit=_parse_limit(options),
        force_rerun="--force-rerun" in flags,
        emit_pre_qwen_preview="--emit-pre-qwen-preview" in flags,
        detector_lanes=detector_lanes or ACTIVE_DETECTOR_LANES,
        followup_request_path=Path(raw_followup) if raw_followup is not None else None,
        project_name=project_name,
    )
