from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from struct import pack
from zlib import compress, crc32

import pytest

from modules import preprocessing


def _chunk(name: bytes, payload: bytes) -> bytes:
    return (
        pack(">I", len(payload))
        + name
        + payload
        + pack(">I", crc32(name + payload) & 0xFFFFFFFF)
    )


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    + _chunk(b"IHDR", pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    + _chunk(b"IDAT", compress(b"\x00\x00\x00\x00\x00"))
    + _chunk(b"IEND", b"")
)


def _write_image(path: Path, content: bytes = PNG_BYTES) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(content)
    return path


def _request(workspace: Path, *arguments: str) -> preprocessing.RunRequest:
    return preprocessing.parse_run_request(
        arguments,
        workspace_root=workspace,
    )


def _fixed_clock() -> datetime:
    return datetime(2026, 8, 2, 15, 30, 45, 123456, tzinfo=timezone(timedelta(hours=9)))


def test_run_request_defaults_run_root_to_timestamped_output(tmp_path: Path) -> None:
    # Given: a workspace image and no explicit output root.
    workspace = tmp_path / "workspace"
    image = _write_image(workspace / "inputs" / "artifact.png")

    # When: the CLI request is parsed with an injected clock.
    request = preprocessing.parse_run_request(
        (str(image.relative_to(workspace)),),
        workspace_root=workspace,
        clock=_fixed_clock,
    )

    # Then: outputs default under a deterministic timestamp folder.
    assert request.run_root == (
        workspace / "output" / "preprocessing" / "20260802-153045-123456"
    )


def test_run_request_preserves_explicit_run_root_when_clock_is_supplied(
    tmp_path: Path,
) -> None:
    # Given: a workspace image and an explicit run root.
    workspace = tmp_path / "workspace"
    image = _write_image(workspace / "inputs" / "artifact.png")

    # When: the CLI request is parsed with an injected clock.
    request = preprocessing.parse_run_request(
        (str(image.relative_to(workspace)), "--run-root", "runs/current"),
        workspace_root=workspace,
        clock=_fixed_clock,
    )

    # Then: the explicit root wins over the timestamp default.
    assert request.run_root == workspace / "runs" / "current"


def test_run_request_project_name_selects_directory_and_named_output(
    tmp_path: Path,
) -> None:
    # Given: a workspace project directory with two input images.
    workspace = tmp_path / "workspace"
    first = _write_image(workspace / "selected2" / "first.png")
    second = _write_image(workspace / "selected2" / "second.jpg")

    # When: the CLI request is parsed with only the project name.
    request = preprocessing.parse_run_request(
        ("--project-name", "selected2"),
        workspace_root=workspace,
        clock=_fixed_clock,
    )

    # Then: project files are selected and output uses the same project name.
    assert request.project_name == "selected2"
    assert request.run_root == workspace / "output" / "preprocessing" / "selected2"
    assert request.image_paths == (first.resolve(), second.resolve())


def test_run_request_project_name_does_not_override_explicit_images_or_run_root(
    tmp_path: Path,
) -> None:
    # Given: a named project and an explicitly selected image/run root.
    workspace = tmp_path / "workspace"
    explicit = _write_image(workspace / "inputs" / "artifact.png")
    _ = _write_image(workspace / "selected2" / "ignored.png")

    # When: explicit image paths and run root are provided with the project name.
    request = preprocessing.parse_run_request(
        (
            str(explicit.relative_to(workspace)),
            "--project-name",
            "selected2",
            "--run-root",
            "runs/manual",
        ),
        workspace_root=workspace,
        clock=_fixed_clock,
    )

    # Then: explicit CLI paths win while project metadata is retained.
    assert request.project_name == "selected2"
    assert request.run_root == workspace / "runs" / "manual"
    assert request.image_paths == (explicit.resolve(),)


def test_run_request_parses_cli_contract_and_preflight_receipt_is_stable(
    tmp_path: Path,
) -> None:
    # Given: a workspace-contained readable input image and every public option.
    workspace = tmp_path / "workspace"
    image = _write_image(workspace / "inputs" / "artifact.png")
    followup_path = Path("requests/followup.json")
    arguments = (
        str(image.relative_to(workspace)),
        "--run-root",
        "runs/current",
        "--device",
        "mps",
        "--qwen-device",
        "cpu",
        "--prompt-pack",
        "review-pack",
        "--dry-run",
        "--limit",
        "7",
        "--force-rerun",
        "--emit-pre-qwen-preview",
        "--detector-lane",
        "owlv2_sam2",
        "--followup-request",
        str(followup_path),
    )

    # When: the CLI request is parsed and preflight runs twice.
    request = _request(workspace, *arguments)
    first_receipt = preprocessing.preflight_run(request)
    second_receipt = preprocessing.preflight_run(request)

    # Then: all input-only fields remain typed, preserved, and deterministic.
    assert request.followup_request_path == followup_path
    assert request.asset_policy is preprocessing.AssetPolicy.COPY
    assert request.limit == 7
    assert request.dry_run is True
    assert request.detector_lanes == (preprocessing.DetectorLane.OWLV2_SAM2,)
    assert isinstance(first_receipt, preprocessing.PreflightReceipt)
    assert first_receipt == second_receipt
    assert first_receipt.exit_code is preprocessing.ExitCode.OK


def test_preflight_rejects_unsafe_roots_and_source_paths(tmp_path: Path) -> None:
    # Given: one workspace image, an outside image, and protected output locations.
    workspace = tmp_path / "workspace"
    image = _write_image(workspace / "inputs" / "artifact.png")
    outside_image = _write_image(tmp_path / "outside.png")
    protected = "runs/local_mac_selected7_20260724T161043256175Z"

    # When: each invalid path reaches the preflight boundary before any materialization.
    outside_run = preprocessing.preflight_run(
        _request(workspace, str(image.relative_to(workspace)), "--run-root", "../run")
    )
    protected_root = preprocessing.preflight_run(
        _request(workspace, str(image.relative_to(workspace)), "--run-root", protected)
    )
    protected_child = preprocessing.preflight_run(
        _request(
            workspace,
            str(image.relative_to(workspace)),
            "--run-root",
            f"{protected}/child",
        )
    )
    outside_source = preprocessing.preflight_run(
        _request(workspace, str(outside_image), "--run-root", "runs/current")
    )

    # Then: each returns the shared incomplete/failure exit code and no valid receipt.
    for receipt in (outside_run, protected_root, protected_child, outside_source):
        assert isinstance(receipt, preprocessing.PreflightFailureReceipt)
        assert receipt.exit_code is preprocessing.ExitCode.INCOMPLETE_OR_FAILURE


def test_preflight_rejects_symlink_escapes(tmp_path: Path) -> None:
    # Given: workspace-local symlinks that resolve outside the workspace boundary.
    workspace = tmp_path / "workspace"
    image = _write_image(workspace / "inputs" / "artifact.png")
    outside_image = _write_image(tmp_path / "outside.png")
    external_run_root = tmp_path / "external-run-root"
    external_run_root.mkdir()
    source_link = workspace / "inputs" / "source-link.png"
    source_link.symlink_to(outside_image)
    run_root_link = workspace / "runs" / "linked-root"
    run_root_link.parent.mkdir(parents=True)
    run_root_link.symlink_to(external_run_root, target_is_directory=True)

    # When: preflight resolves source and run-root links before materialization.
    source_escape = preprocessing.preflight_run(
        _request(workspace, "inputs/source-link.png", "--run-root", "runs/current")
    )
    run_root_escape = preprocessing.preflight_run(
        _request(
            workspace,
            str(image.relative_to(workspace)),
            "--run-root",
            "runs/linked-root",
        )
    )

    # Then: both escapes fail closed with the shared non-success exit code.
    for receipt in (source_escape, run_root_escape):
        assert isinstance(receipt, preprocessing.PreflightFailureReceipt)
        assert receipt.exit_code is preprocessing.ExitCode.INCOMPLETE_OR_FAILURE


def test_preflight_rejects_empty_duplicate_and_non_image_inputs(tmp_path: Path) -> None:
    # Given: a valid PNG and a readable non-image source file.
    workspace = tmp_path / "workspace"
    image = _write_image(workspace / "inputs" / "artifact.png")
    non_image = _write_image(workspace / "inputs" / "notes.txt", b"not an image")

    # When: preflight receives empty, duplicated, and non-image input lists.
    empty = preprocessing.preflight_run(
        _request(workspace, "--run-root", "runs/current")
    )
    duplicate = preprocessing.preflight_run(
        _request(
            workspace,
            str(image.relative_to(workspace)),
            str(image.relative_to(workspace)),
            "--run-root",
            "runs/current",
        )
    )
    non_image_receipt = preprocessing.preflight_run(
        _request(
            workspace,
            str(non_image.relative_to(workspace)),
            "--run-root",
            "runs/current",
        )
    )
    missing = preprocessing.preflight_run(
        _request(workspace, "inputs/missing.png", "--run-root", "runs/current")
    )

    # Then: each input failure is represented without raising or writing assets.
    for receipt in (empty, duplicate, non_image_receipt, missing):
        assert isinstance(receipt, preprocessing.PreflightFailureReceipt)
        assert receipt.exit_code is preprocessing.ExitCode.INCOMPLETE_OR_FAILURE


def test_preflight_rejects_structurally_malformed_png(tmp_path: Path) -> None:
    # Given: a source that has a PNG signature but no valid chunk structure.
    workspace = tmp_path / "workspace"
    malformed = _write_image(workspace / "inputs" / "malformed.png", PNG_BYTES[:8])

    # When: preflight performs the image readability check.
    receipt = preprocessing.preflight_run(
        _request(
            workspace,
            str(malformed.relative_to(workspace)),
            "--run-root",
            "runs/current",
        )
    )

    # Then: magic bytes alone cannot make malformed image data readable.
    assert isinstance(receipt, preprocessing.PreflightFailureReceipt)
    assert receipt.exit_code is preprocessing.ExitCode.INCOMPLETE_OR_FAILURE


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("malformed.jpg", b"\xff\xd8\xff\xc0\x00\x08\x08\x00\x01\x00\x01\x00\xff\xd9"),
        ("malformed.gif", b"GIF89a\x01\x00\x01\x00\x00\x00\x00;"),
    ],
)
def test_preflight_rejects_structurally_malformed_jpeg_and_gif(
    tmp_path: Path,
    filename: str,
    content: bytes,
) -> None:
    # Given: format signatures without a complete JPEG segment or GIF image block.
    workspace = tmp_path / "workspace"
    malformed = _write_image(workspace / "inputs" / filename, content)

    # When: each image reaches the deterministic structural checker.
    receipt = preprocessing.preflight_run(
        _request(
            workspace,
            str(malformed.relative_to(workspace)),
            "--run-root",
            "runs/current",
        )
    )

    # Then: every malformed supported format fails closed before manifesting.
    assert isinstance(receipt, preprocessing.PreflightFailureReceipt)


def test_preflight_rejects_clipseg_and_non_production_qwen_backends(
    tmp_path: Path,
) -> None:
    # Given: a valid source image with a forbidden lane or fake backend selection.
    workspace = tmp_path / "workspace"
    image = _write_image(workspace / "inputs" / "artifact.png")
    clipseg_request = _request(
        workspace,
        str(image.relative_to(workspace)),
        "--run-root",
        "runs/current",
        "--detector-lane",
        "clipseg",
    )
    request = _request(
        workspace,
        str(image.relative_to(workspace)),
        "--run-root",
        "runs/current",
    )

    # When: preflight validates lanes and the shared production backend kind.
    clipseg = preprocessing.preflight_run(clipseg_request)
    fake = preprocessing.preflight_run(
        request,
        settings=preprocessing.PreflightSettings(qwen_backend_kind="fake"),
    )
    selected7 = preprocessing.preflight_run(
        request,
        settings=preprocessing.PreflightSettings(qwen_backend_kind="selected7"),
    )

    # Then: excluded and non-production execution configurations fail closed.
    for receipt in (clipseg, fake, selected7):
        assert isinstance(receipt, preprocessing.PreflightFailureReceipt)
        assert receipt.exit_code is preprocessing.ExitCode.INCOMPLETE_OR_FAILURE
