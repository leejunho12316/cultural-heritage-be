from __future__ import annotations

# pyright: reportAny=false
import json
from pathlib import Path
from struct import pack
from typing import TYPE_CHECKING
from zlib import compress, crc32

import numpy as np
import pytest
import torch

from modules import preprocessing
from modules.preprocessing.model_runtime.inventory import ModelInventoryEntry
from modules.preprocessing.model_runtime.runtime import resolve_runtime_device
from modules.preprocessing.pipeline import run
from modules.shared import ContractValidationError

if TYPE_CHECKING:
    from modules.preprocessing.contracts.records import DetectionBox, DetectionRun
    from modules.preprocessing.model_runtime.options import RealPreprocessingOptions


class NoopProcessor:
    pass


class NoopModel:
    pass


class FakePredictor:
    def set_image(self, image: np.ndarray[tuple[int, ...], np.dtype[np.uint8]]) -> None:
        _ = image

    def predict(
        self,
        *,
        box: np.ndarray[tuple[int, ...], np.dtype[np.float64]],
        multimask_output: bool,
    ) -> tuple[
        tuple[np.ndarray[tuple[int, ...], np.dtype[np.bool_]], ...],
        np.ndarray[tuple[int, ...], np.dtype[np.float32]],
        torch.Tensor,
    ]:
        _ = box, multimask_output
        mask = np.ones((1, 1), dtype=np.bool_)
        scores = np.array([0.9], dtype=np.float32)
        logits = torch.tensor([0.0])
        return (mask,), scores, logits


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


def _model_entries() -> dict[str, ModelInventoryEntry]:
    return {
        "owlv2_sam2.detector": ModelInventoryEntry(
            "owlv2_sam2.detector",
            "google/owlv2-base-patch16-ensemble",
            "main",
            Path("models/hf/google/owlv2-base-patch16-ensemble"),
        ),
        "sam2.segmenter": ModelInventoryEntry(
            "sam2.segmenter",
            "facebook/sam2-hiera-large",
            "main",
            Path("models/hf/facebook/sam2-hiera-large"),
        ),
    }


def test_runtime_device_auto_prefers_mps_when_cuda_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: CUDA is unavailable and Apple MPS is available.
    def cuda_unavailable() -> bool:
        return False

    def mps_available() -> bool:
        return True

    monkeypatch.setattr(torch.cuda, "is_available", cuda_unavailable)
    monkeypatch.setattr(torch.backends.mps, "is_built", mps_available)
    monkeypatch.setattr(torch.backends.mps, "is_available", mps_available)

    # When: real preprocessing resolves the default device.
    device = resolve_runtime_device(preprocessing.Device.AUTO)

    # Then: auto selects the available GPU backend before CPU.
    assert device == "mps"


def test_runtime_device_auto_falls_back_to_cpu_when_gpu_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: neither CUDA nor Apple MPS is available.
    def unavailable() -> bool:
        return False

    monkeypatch.setattr(torch.cuda, "is_available", unavailable)
    monkeypatch.setattr(torch.backends.mps, "is_built", unavailable)
    monkeypatch.setattr(torch.backends.mps, "is_available", unavailable)

    # When: real preprocessing resolves the default device.
    device = resolve_runtime_device(preprocessing.Device.AUTO)

    # Then: CPU is selected instead of failing the run at preflight.
    assert device == "cpu"


def test_runtime_device_accepts_explicit_cpu() -> None:
    # Given: CPU is explicitly requested for a real local experiment.
    # When: the runtime resolver checks the requested device.
    device = resolve_runtime_device(preprocessing.Device.CPU)

    # Then: CPU is treated as a supported real runtime backend.
    assert device == "cpu"


def test_runtime_device_rejects_unavailable_explicit_cuda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: CUDA is explicitly requested on a non-CUDA host.
    def cuda_unavailable() -> bool:
        return False

    monkeypatch.setattr(torch.cuda, "is_available", cuda_unavailable)

    # When: the runtime resolver checks the requested device.
    with pytest.raises(ContractValidationError) as error:
        _ = resolve_runtime_device(preprocessing.Device.CUDA)

    # Then: the failure is structured at the device boundary.
    assert error.value.field == "device"
    assert "cuda" in error.value.reason


def test_real_pipeline_dry_run_does_not_resolve_gpu_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a dry-run request and a GPU resolver that would fail if called.
    workspace = tmp_path / "workspace"
    image = _write_image(workspace / "inputs" / "artifact.png")
    run_root = workspace / "runs" / "dry"

    def fail_device_resolution(device: preprocessing.Device) -> str:
        _ = device
        message = "GPU device resolved during dry-run"
        raise AssertionError(message)

    monkeypatch.chdir(workspace)
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.resolve_runtime_device",
        fail_device_resolution,
    )

    # When: the real preprocessing CLI is invoked with dry-run.
    exit_code = run(
        (
            str(image.relative_to(workspace)),
            "--run-root",
            str(run_root.relative_to(workspace)),
            "--dry-run",
        )
    )

    # Then: no GPU availability check is performed.
    manifest = json.loads(
        (run_root / "manifests" / "real_preprocessing_manifest.json").read_text()
    )
    assert exit_code == preprocessing.ExitCode.OK
    assert manifest["device"] == "not_executed_dry_run"


def test_real_pipeline_passes_selected_gpu_device_to_model_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a valid real request and fake runtime seams that record the device.
    workspace = tmp_path / "workspace"
    image = _write_image(workspace / "inputs" / "artifact.png")
    run_root = workspace / "runs" / "mps"
    loaded_devices: list[str] = []
    monkeypatch.chdir(workspace)

    def load_inventory(
        options: RealPreprocessingOptions,
    ) -> dict[str, ModelInventoryEntry]:
        _ = options
        return _model_entries()

    def validate_cache(entries: dict[str, ModelInventoryEntry]) -> None:
        _ = entries

    def load_models(
        entries: dict[str, ModelInventoryEntry], device: str
    ) -> tuple[NoopProcessor, NoopModel, FakePredictor]:
        _ = entries
        loaded_devices.append(device)
        return NoopProcessor(), NoopModel(), FakePredictor()

    def resolve_mps(device: preprocessing.Device) -> str:
        _ = device
        return "mps"

    def no_detections(
        image_path: Path,
        detection_run: DetectionRun[NoopProcessor, NoopModel],
    ) -> tuple[DetectionBox, ...]:
        _ = image_path, detection_run
        return ()

    monkeypatch.setattr(
        "modules.preprocessing.pipeline.resolve_runtime_device",
        resolve_mps,
    )
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.load_model_inventory",
        load_inventory,
    )
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.validate_model_cache",
        validate_cache,
    )
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.load_runtime_models",
        load_models,
    )
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.run_runtime_detection",
        no_detections,
    )

    # When: real preprocessing is asked to use MPS.
    exit_code = run(
        (
            str(image.relative_to(workspace)),
            "--run-root",
            str(run_root.relative_to(workspace)),
            "--device",
            "mps",
            "--detector-lane",
            "owlv2_sam2",
        )
    )

    # Then: all real model loaders receive the selected GPU device.
    manifest = json.loads(
        (run_root / "manifests" / "real_preprocessing_manifest.json").read_text()
    )
    assert exit_code == preprocessing.ExitCode.OK
    assert loaded_devices == ["mps"]
    assert manifest["device"] == "mps"
    assert manifest["images"][0]["scale_metadata"]["scale_confidence"] == "unavailable"
    assert manifest["images"][0]["scale_removal_applied"] is False
    assert manifest["images"][0]["detector_input"]["media_type"] == "image/jpeg"
