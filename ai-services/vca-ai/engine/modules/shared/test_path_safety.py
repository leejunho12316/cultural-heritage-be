from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from modules.shared import (
    PathSafetyError,
    ensure_asset_path,
    ensure_final_report_write_paths,
    ensure_no_symlink_leaf,
    ensure_safe_run_root,
    ensure_source_document_is_not_write_target,
)


def test_run_root_rejects_protected_baseline_and_descendants(tmp_path: Path) -> None:
    # Given: a workspace with a protected baseline location.
    workspace_root = tmp_path / "workspace"
    protected_baseline = (
        workspace_root / "runs/local_mac_selected7_20260724T161043256175Z"
    )
    workspace_root.mkdir()

    # When: safe and protected run roots are checked before any write.
    safe_run_root = ensure_safe_run_root(
        workspace_root, workspace_root / "runs/new-run"
    )

    # Then: safe roots are accepted while baseline root and child paths fail closed.
    assert safe_run_root == (workspace_root / "runs/new-run").resolve()
    with pytest.raises(PathSafetyError):
        _ = ensure_safe_run_root(workspace_root, protected_baseline)
    with pytest.raises(PathSafetyError):
        _ = ensure_safe_run_root(workspace_root, protected_baseline / "child")


def test_asset_paths_reject_absolute_parent_and_symlink_escapes(tmp_path: Path) -> None:
    # Given: a contained asset root and an outside file.
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    outside_file = tmp_path / "outside.png"
    _ = outside_file.write_text("outside")

    # When: valid and adversarial asset paths are resolved.
    asset_path = ensure_asset_path(asset_root, "nested/mask.png")

    # Then: only contained relative paths survive validation.
    assert asset_path == (asset_root / "nested/mask.png").resolve()
    with pytest.raises(PathSafetyError):
        _ = ensure_asset_path(asset_root, "/var/outside.png")
    with pytest.raises(PathSafetyError):
        _ = ensure_asset_path(asset_root, "../outside.png")
    escaped_link = asset_root / "escape.png"
    escaped_link.symlink_to(outside_file)
    with pytest.raises(PathSafetyError):
        _ = ensure_asset_path(asset_root, "escape.png")


def test_final_report_symlinks_and_source_document_writes_are_rejected(
    tmp_path: Path,
) -> None:
    # Given: a run root, a source document root, and a symlinked final-report leaf.
    run_root = tmp_path / "run"
    final_report_root = run_root / "final_report"
    source_documents = tmp_path / "source-documents"
    run_root.mkdir()
    final_report_root.mkdir()
    source_documents.mkdir()
    outside_file = tmp_path / "outside.html"
    _ = outside_file.write_text("outside")
    (final_report_root / "index.html").symlink_to(outside_file)

    # When: pre-write safety guards run.
    with pytest.raises(PathSafetyError):
        _ = ensure_final_report_write_paths(run_root, final_report_root)

    # Then: source document directories cannot become write targets either.
    with pytest.raises(PathSafetyError):
        _ = ensure_source_document_is_not_write_target(
            source_documents, source_documents / "cache.json"
        )


def test_final_report_root_symlink_is_rejected(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    external_root = tmp_path / "external"
    run_root.mkdir()
    external_root.mkdir()
    final_report_root = run_root / "final_report"
    final_report_root.symlink_to(external_root, target_is_directory=True)

    with pytest.raises(PathSafetyError):
        _ = ensure_final_report_write_paths(run_root, final_report_root)


def test_write_leaf_guard_accepts_missing_and_regular_leaf(tmp_path: Path) -> None:
    # Given: one missing write leaf and one regular write leaf.
    missing_leaf = tmp_path / "sidecar.json"
    regular_leaf = tmp_path / "existing.json"
    _ = regular_leaf.write_text("{}", encoding="utf-8")

    # When: write leaf symlink guards run.
    missing_result = ensure_no_symlink_leaf(missing_leaf, "sidecar leaf is a symlink")
    regular_result = ensure_no_symlink_leaf(regular_leaf, "sidecar leaf is a symlink")

    # Then: non-symlink leaves are returned unchanged for writer use.
    assert missing_result == missing_leaf
    assert regular_result == regular_leaf


def test_write_leaf_guard_rejects_final_and_temp_leaf_symlinks(
    tmp_path: Path,
) -> None:
    # Given: final and temp write leaves point at external sentinel files.
    final_sentinel = tmp_path / "final-sentinel.json"
    temp_sentinel = tmp_path / "temp-sentinel.json"
    _ = final_sentinel.write_text("final", encoding="utf-8")
    _ = temp_sentinel.write_text("temp", encoding="utf-8")
    final_leaf = tmp_path / "sidecar.json"
    temp_leaf = tmp_path / "sidecar.json.tmp"
    final_leaf.symlink_to(final_sentinel)
    temp_leaf.symlink_to(temp_sentinel)

    # When/Then: both final and temp symlink leaves fail closed.
    with pytest.raises(PathSafetyError):
        _ = ensure_no_symlink_leaf(final_leaf, "sidecar leaf is a symlink")
    with pytest.raises(PathSafetyError):
        _ = ensure_no_symlink_leaf(temp_leaf, "sidecar leaf is a symlink")
