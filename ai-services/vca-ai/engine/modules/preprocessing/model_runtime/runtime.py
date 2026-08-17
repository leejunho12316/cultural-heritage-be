"""Deferred import boundary for heavyweight preprocessing runtime."""

# pyright: reportAny=false, reportUnknownMemberType=false

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from modules.preprocessing.preflight.request import Device
from modules.shared import ContractValidationError

if TYPE_CHECKING:
    from pathlib import Path

    from modules.preprocessing.contracts.records import DetectionBox, DetectionRun
    from modules.preprocessing.model_runtime.inventory import ModelInventoryEntry
    from modules.preprocessing.model_runtime.models import (
        OwlModel,
        OwlProcessor,
        SamPredictor,
    )


def _unavailable_device(device: str) -> ContractValidationError:
    field = "device"
    reason = f"requested GPU device unavailable: {device}"
    return ContractValidationError(field, reason)


def resolve_runtime_device(request_device: Device) -> str:
    """Resolve the requested real-run device to an available backend."""
    torch = import_module("torch")
    match request_device:
        case Device.AUTO:
            if torch.cuda.is_available():
                return "cuda"
            if torch.backends.mps.is_built() and torch.backends.mps.is_available():
                return "mps"
            return "cpu"
        case Device.CUDA:
            if torch.cuda.is_available():
                return "cuda"
            device = "cuda"
            raise _unavailable_device(device)
        case Device.MPS:
            if torch.backends.mps.is_built() and torch.backends.mps.is_available():
                return "mps"
            device = "mps"
            raise _unavailable_device(device)
        case Device.CPU:
            return "cpu"


def load_runtime_models(
    model_entries: dict[str, ModelInventoryEntry], device: str
) -> tuple[OwlProcessor, OwlModel, SamPredictor]:
    """Import and load heavy models only after dry-run gates pass."""
    module = import_module("modules.preprocessing.model_runtime.models")
    return module.load_real_models(model_entries, device)


def run_runtime_detection(
    image_path: Path,
    detection_run: DetectionRun[OwlProcessor, OwlModel],
) -> tuple[DetectionBox, ...]:
    """Import and run the heavy detector only during real execution."""
    module = import_module("modules.preprocessing.model_runtime.models")
    return module.detect_boxes(image_path, detection_run)
