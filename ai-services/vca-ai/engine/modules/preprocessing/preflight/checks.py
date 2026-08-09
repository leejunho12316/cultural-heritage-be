"""Filesystem and configuration checks before preprocessing materialization."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from modules.preprocessing.preflight.readability import readable_mime_type
from modules.preprocessing.preflight.request import AssetPolicy, RunRequest
from modules.shared import (
    QWEN_BACKEND_KIND,
    ContractValidationError,
    DetectorLane,
    ExitCode,
    PathSafetyError,
    ensure_safe_run_root,
    validate_active_detector_lanes,
)


class Readability(StrEnum):
    """Readability state recorded for accepted input assets."""

    READABLE = "readable"


@dataclass(frozen=True, slots=True)
class PreflightSettings:
    """Explicit non-I/O backend configuration checked at the boundary."""

    qwen_backend_kind: str = QWEN_BACKEND_KIND


@dataclass(frozen=True, slots=True)
class PreflightIssue:
    """One input or configuration reason for a failed preflight."""

    field: str
    reason: str


@dataclass(frozen=True, slots=True)
class PreflightImage:
    """A readable, workspace-contained source image ready for manifesting."""

    source_path: Path
    source_relative_path: str
    mime_type: str
    readability: Readability = Readability.READABLE


@dataclass(frozen=True, slots=True)
class PreflightReceipt:
    """Successful preflight data consumed by the manifest builder."""

    workspace_root: Path
    run_root: Path
    images: tuple[PreflightImage, ...]
    detector_lanes: tuple[DetectorLane, ...]
    qwen_backend_kind: str
    asset_policy: AssetPolicy
    exit_code: ExitCode = ExitCode.OK


@dataclass(frozen=True, slots=True)
class PreflightFailureReceipt:
    """Typed failure receipt for CLI exit-code mapping without output writes."""

    workspace_root: Path
    run_root: Path
    issues: tuple[PreflightIssue, ...]
    exit_code: ExitCode = ExitCode.INCOMPLETE_OR_FAILURE


# 이미지 하나가 워크스페이스 안에 있고, 실제 읽을 수 있는 파일이며, 지원되는
# 이미지 포맷인지 검사해 PreflightImage 또는 PreflightIssue를 반환한다.
# _validated_images에서 이미지마다 호출된다.
def _validate_image(
    path: Path, workspace_root: Path
) -> PreflightImage | PreflightIssue:
    if not path.is_relative_to(workspace_root):
        return PreflightIssue("image_path", f"outside workspace: {path}")
    if not path.is_file():
        return PreflightIssue("image_path", f"not a readable file: {path}")
    try:
        with path.open("rb") as source_file:
            mime_type = readable_mime_type(source_file.read())
    except OSError as error:
        return PreflightIssue("image_path", f"unreadable: {path}: {error.strerror}")
    if mime_type is None:
        return PreflightIssue("image_path", f"unsupported or unreadable image: {path}")
    return PreflightImage(
        source_path=path,
        source_relative_path=path.relative_to(workspace_root).as_posix(),
        mime_type=mime_type,
    )


# run_root가 workspace_root를 벗어나지 않는지 검증하고, 실패 시에도 로깅용
# 경로와 이슈 목록을 함께 돌려준다. preflight_run에서 호출된다.
def _safe_run_root(
    request: RunRequest, workspace_root: Path
) -> tuple[Path, tuple[PreflightIssue, ...]]:
    run_root = request.run_root.resolve()
    if not workspace_root.is_dir():
        issue = PreflightIssue("workspace_root", "must be an existing directory")
        return run_root, (issue,)
    try:
        return ensure_safe_run_root(workspace_root, request.run_root), ()
    except PathSafetyError as error:
        return run_root, (PreflightIssue("run_root", error.reason),)


# 요청된 이미지 경로들을 중복 제거하며 하나씩 검증해 통과한 이미지와
# 이슈 목록을 나눠 반환한다. preflight_run에서 호출된다.
def _validated_images(
    request: RunRequest, workspace_root: Path
) -> tuple[tuple[PreflightImage, ...], tuple[PreflightIssue, ...]]:
    if not request.image_paths:
        issue = PreflightIssue("image_paths", "must not be empty")
        return (), (issue,)
    seen_paths: set[Path] = set()
    images: list[PreflightImage] = []
    issues: list[PreflightIssue] = []
    for path in request.image_paths:
        if path in seen_paths:
            issues.append(PreflightIssue("image_paths", f"duplicate input: {path}"))
            continue
        seen_paths.add(path)
        image_or_issue = _validate_image(path, workspace_root)
        match image_or_issue:
            case PreflightImage() as image:
                images.append(image)
            case PreflightIssue() as issue:
                issues.append(issue)
    return tuple(images), tuple(issues)


# 요청된 detector_lanes가 활성 레인 목록에 포함되는지 검증한다.
# preflight_run에서 호출된다.
def _validated_lanes(
    request: RunRequest,
) -> tuple[tuple[DetectorLane, ...], tuple[PreflightIssue, ...]]:
    try:
        return validate_active_detector_lanes(request.detector_lanes), ()
    except ContractValidationError as error:
        return (), (PreflightIssue(error.field, error.reason),)


def preflight_run(
    request: RunRequest, *, settings: PreflightSettings | None = None
) -> PreflightReceipt | PreflightFailureReceipt:
    """Validate all T3 preflight conditions without materializing any assets."""
    selected_settings = settings if settings is not None else PreflightSettings()
    workspace_root = request.workspace_root.resolve()
    run_root, root_issues = _safe_run_root(request, workspace_root)
    images, image_issues = _validated_images(request, workspace_root)
    detector_lanes, lane_issues = _validated_lanes(request)
    issues = (*root_issues, *image_issues, *lane_issues)
    if selected_settings.qwen_backend_kind != QWEN_BACKEND_KIND:
        issues = (
            *issues,
            PreflightIssue("qwen_backend_kind", f"must equal {QWEN_BACKEND_KIND}"),
        )
    if issues:
        return PreflightFailureReceipt(
            workspace_root=workspace_root,
            run_root=run_root,
            issues=issues,
        )
    return PreflightReceipt(
        workspace_root=workspace_root,
        run_root=run_root,
        images=images,
        detector_lanes=detector_lanes,
        qwen_backend_kind=selected_settings.qwen_backend_kind,
        asset_policy=request.asset_policy,
    )
