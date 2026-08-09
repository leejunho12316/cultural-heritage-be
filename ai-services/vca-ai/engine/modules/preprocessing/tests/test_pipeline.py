from __future__ import annotations

# pyright: reportAny=false
import json
from datetime import datetime, timedelta, timezone
from struct import pack
from typing import TYPE_CHECKING
from zlib import compress, crc32

from modules import preprocessing
from modules.preprocessing.pipeline import run

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


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


def _write_image(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(PNG_BYTES)
    return path


def _fixed_clock() -> datetime:
    return datetime(2026, 8, 2, 15, 30, 45, 123456, tzinfo=timezone(timedelta(hours=9)))


def _write_inventory(root: Path) -> None:
    inventory_dir = root / "inventory"
    inventory_dir.mkdir(parents=True)
    _ = (inventory_dir / "model_inventory.json").write_text(
        json.dumps(
            {
                "schema_version": "vca-shared-model-inventory-v1",
                "models": [
                    {
                        "key": "owlv2_sam2.detector",
                        "repo_id": "google/owlv2-base-patch16-ensemble",
                        "revision": "main",
                        "local_dir": "models/hf/google/owlv2-base-patch16-ensemble",
                    },
                    {
                        "key": "sam2.segmenter",
                        "repo_id": "facebook/sam2-hiera-large",
                        "revision": "main",
                        "local_dir": "models/hf/facebook/sam2-hiera-large",
                    },
                ],
            }
        )
    )


def test_real_pipeline_help_prints_execution_manual(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a preprocessing operator asks for CLI usage guidance.
    arguments = ("--help",)

    # When: the real preprocessing entrypoint handles the help request.
    exit_code = run(arguments)

    # Then: the printed manual includes canonical image and project commands.
    captured = capsys.readouterr()
    assert exit_code == preprocessing.ExitCode.OK
    assert "Real preprocessing execution manual" in captured.out
    assert "python -m modules.preprocessing.pipeline" in captured.out
    assert "--project-name selected2" in captured.out
    assert "--foreground-white-threshold 215" in captured.out


def test_real_pipeline_dry_run_does_not_import_heavy_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a valid image request and a runtime loader that would fail if called.
    workspace = tmp_path / "workspace"
    image = _write_image(workspace / "inputs" / "artifact.png")
    run_root = workspace / "runs" / "dry"

    def fail_runtime() -> object:
        message = "heavy runtime imported during dry-run"
        raise AssertionError(message)

    def fail_scale_preprocessing(
        image_path: Path, run_root: Path, image_id: str
    ) -> object:
        _ = image_path, run_root, image_id
        message = "scale preprocessing executed during dry-run"
        raise AssertionError(message)

    monkeypatch.chdir(workspace)
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.prepare_detector_input",
        fail_scale_preprocessing,
    )
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.load_runtime_models",
        fail_runtime,
    )
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.run_runtime_detection",
        fail_runtime,
    )

    # When: the real preprocessing CLI is invoked with the existing dry-run flag.
    exit_code = run(
        (
            str(image.relative_to(workspace)),
            "--run-root",
            str(run_root.relative_to(workspace)),
            "--dry-run",
        )
    )

    # Then: dry-run emits a zero-execution real manifest without heavy imports.
    raw_manifest: dict[str, object] = json.loads(
        (run_root / "manifests" / "real_preprocessing_manifest.json").read_text()
    )
    assert exit_code == preprocessing.ExitCode.OK
    assert raw_manifest["detector_lane_status"] == "dry_run_not_executed"
    assert raw_manifest["model_invocations"] == 0
    assert raw_manifest["sam2_calls"] == 0
    assert raw_manifest["objects"] == []


def test_real_pipeline_dry_run_defaults_to_timestamped_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a valid dry-run request without an explicit run root.
    workspace = tmp_path / "workspace"
    image = _write_image(workspace / "inputs" / "artifact.png")
    expected_run_root = (
        workspace / "output" / "preprocessing" / "20260802-153045-123456"
    )
    monkeypatch.chdir(workspace)

    # When: the real preprocessing CLI is invoked with an injected clock.
    exit_code = run(
        (str(image.relative_to(workspace)), "--dry-run"),
        clock=_fixed_clock,
    )

    # Then: dry-run materializes manifests under the timestamped default root.
    raw_manifest: dict[str, object] = json.loads(
        (
            expected_run_root / "manifests" / "real_preprocessing_manifest.json"
        ).read_text()
    )
    assert exit_code == preprocessing.ExitCode.OK
    assert raw_manifest["detector_lane_status"] == "dry_run_not_executed"


def test_real_pipeline_rejects_unsupported_real_lane_before_model_loading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a valid image request for a non-v1 real lane.
    workspace = tmp_path / "workspace"
    image = _write_image(workspace / "inputs" / "artifact.png")
    run_root = workspace / "runs" / "grounded"
    monkeypatch.chdir(workspace)

    # When: real preprocessing is asked to execute an unsupported lane.
    exit_code = run(
        (
            str(image.relative_to(workspace)),
            "--run-root",
            str(run_root.relative_to(workspace)),
            "--detector-lane",
            "grounded_sam2",
        )
    )

    # Then: the failure is structured and no model cache lookup is required.
    failure = json.loads(
        (run_root / "receipts" / "real_preprocessing_failure.json").read_text()
    )
    assert exit_code == preprocessing.ExitCode.INCOMPLETE_OR_FAILURE
    assert failure["field"] == "detector_lanes"


def test_real_pipeline_accepts_cpu_until_model_cache_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a valid image request with CPU selected for real execution.
    workspace = tmp_path / "workspace"
    image = _write_image(workspace / "inputs" / "artifact.png")
    run_root = workspace / "runs" / "cpu"
    inventory_root = workspace / "models"
    _write_inventory(inventory_root)
    monkeypatch.chdir(workspace)

    def resolve_cpu(_requested_device: preprocessing.Device) -> str:
        return "cpu"

    monkeypatch.setattr(
        "modules.preprocessing.pipeline.resolve_runtime_device",
        resolve_cpu,
    )

    # When: real preprocessing is asked to execute on CPU.
    exit_code = run(
        (
            str(image.relative_to(workspace)),
            "--run-root",
            str(run_root.relative_to(workspace)),
            "--device",
            "cpu",
            "--detector-lane",
            "owlv2_sam2",
            "--model-cache-root",
            str(inventory_root.relative_to(workspace)),
        )
    )

    # Then: CPU passes device validation and reaches model-cache validation.
    failure = json.loads(
        (run_root / "receipts" / "real_preprocessing_failure.json").read_text()
    )
    assert exit_code == preprocessing.ExitCode.INCOMPLETE_OR_FAILURE
    assert failure["field"] == "model_inventory.models.owlv2_sam2.detector.local_dir"
