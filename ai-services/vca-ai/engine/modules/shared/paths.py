"""Pre-write path containment and symlink safety guards."""

from pathlib import Path, PurePath

from modules.shared.constants import PROTECTED_BASELINE_RELATIVE_PATH
from modules.shared.errors import PathSafetyError


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def _is_contained(candidate: Path, root: Path) -> bool:
    return candidate == root or candidate.is_relative_to(root)


def ensure_safe_run_root(workspace_root: Path, run_root: Path) -> Path:
    """Require a workspace-contained run root outside the protected baseline."""
    resolved_workspace = _resolved(workspace_root)
    resolved_run_root = _resolved(run_root)
    protected_root = resolved_workspace / PROTECTED_BASELINE_RELATIVE_PATH
    if not _is_contained(resolved_run_root, resolved_workspace):
        raise PathSafetyError(str(run_root), "run root is outside workspace")
    if _is_contained(resolved_run_root, protected_root):
        raise PathSafetyError(str(run_root), "protected baseline or descendant")
    return resolved_run_root


def _safe_relative_asset_path(relative_path: str) -> PurePath:
    candidate = PurePath(relative_path)
    if not relative_path or candidate.is_absolute() or ".." in candidate.parts:
        raise PathSafetyError(
            relative_path, "asset path must be contained and relative"
        )
    return candidate


def ensure_asset_path(asset_root: Path, relative_path: str) -> Path:
    """Resolve an asset path only when it remains inside its declared root."""
    safe_relative_path = _safe_relative_asset_path(relative_path)
    resolved_root = _resolved(asset_root)
    resolved_asset = _resolved(resolved_root / safe_relative_path)
    if not _is_contained(resolved_asset, resolved_root):
        raise PathSafetyError(relative_path, "asset path escapes asset root")
    return resolved_asset


def ensure_final_report_write_paths(
    run_root: Path, final_report_root: Path
) -> tuple[Path, Path]:
    """Reject final-report roots or protected leaves that are symlinks or escape."""
    resolved_run_root = _resolved(run_root)
    if final_report_root.is_symlink():
        raise PathSafetyError(str(final_report_root), "final report root is a symlink")
    resolved_final_root = _resolved(final_report_root)
    if not _is_contained(resolved_final_root, resolved_run_root):
        raise PathSafetyError(
            str(final_report_root), "final report root escapes run root"
        )
    index_path = final_report_root / "index.html"
    metadata_path = final_report_root / "metadata.json"
    for leaf_path in (index_path, metadata_path):
        if leaf_path.is_symlink():
            raise PathSafetyError(str(leaf_path), "final report leaf is a symlink")
    return _resolved(index_path), _resolved(metadata_path)


def ensure_no_symlink_leaf(path: Path, reason: str) -> Path:
    """Reject a write target leaf that is itself a symlink."""
    if path.is_symlink():
        raise PathSafetyError(str(path), reason)
    return path


def ensure_no_symlink_path_components(path: Path, reason: str) -> Path:
    """Reject a path whose existing components include a symlink."""
    expanded_path = path.expanduser()
    for component in (expanded_path, *expanded_path.parents):
        if component.is_symlink():
            raise PathSafetyError(str(path), reason)
    return path


def ensure_contained_write_path(root: Path, path: Path, reason: str) -> Path:
    """Reject a write path when its leaf or parent chain is unsafe."""
    expanded_root = root.expanduser()
    expanded_path = path.expanduser()
    if _has_symlink_component(expanded_path, expanded_root):
        raise PathSafetyError(str(path), reason)
    resolved_parent = _resolved(path.parent)
    if not _is_contained(resolved_parent, _resolved(root)):
        raise PathSafetyError(str(path), reason)
    return path


def _has_symlink_component(path: Path, stop: Path) -> bool:
    current = path
    while True:
        if current.is_symlink():
            return True
        if current in (stop, current.parent):
            return False
        current = current.parent


def ensure_source_document_is_not_write_target(
    source_document_root: Path, write_target: Path
) -> Path:
    """Forbid writing a cache, sidecar, or artifact inside source documents."""
    resolved_source_root = _resolved(source_document_root)
    resolved_target = _resolved(write_target)
    if _is_contained(resolved_target, resolved_source_root):
        raise PathSafetyError(str(write_target), "source document writes are forbidden")
    return resolved_target
