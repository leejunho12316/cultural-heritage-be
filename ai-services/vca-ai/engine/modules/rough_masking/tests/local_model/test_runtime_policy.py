from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from modules.preprocessing.model_runtime.inventory import ModelInventoryEntry
from modules.rough_masking.local_model.inference import (
    load_grounded_detector,
    load_owlv2_detector,
)
from modules.rough_masking.local_model.runtime import build_lane_runtime
from modules.rough_masking.local_model.segmentation import (
    LocalInferenceSettings,
    load_sam2_predictor,
)
from modules.rough_masking.tests.local_model.test_runtime_fakes import (
    FakeLocalLoader,
    install_model_loader_fakes,
)
from modules.shared import ContractValidationError, DetectorLane


def test_runtime_import_does_not_load_heavy_vision_dependencies() -> None:
    # Given: a clean Python process without an imported local runtime module.
    module_names = "torch transformers sam2 PIL numpy"
    command = (
        "import sys; "
        "import modules.rough_masking.local_model.runtime; "
        f"blocked = {module_names!r}.split(); "
        "raise SystemExit(any(name in sys.modules for name in blocked))"
    )

    # When: the lightweight runtime adapter module is imported.
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", command],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then: importing its construction seam did not import a vision runtime.
    assert result.returncode == 0, result.stderr


def test_build_lane_runtime_does_not_load_a_model() -> None:
    # Given: local detector and SAM2 inventory records.
    detector_entry = ModelInventoryEntry(
        "owlv2_sam2.detector", "unused/remote", "main", Path("/local/detector")
    )
    sam2_entry = ModelInventoryEntry(
        "sam2.segmenter", "unused/remote", "main", Path("/local/sam2")
    )

    # When: the OWLv2 lane adapter is constructed.
    runtime = build_lane_runtime(
        lane=DetectorLane.OWLV2_SAM2,
        detector_entry=detector_entry,
        sam2_entry=sam2_entry,
        device="mps",
    )

    # Then: construction stores configuration only; no detector output exists yet.
    assert type(runtime).__name__ == "Owlv2Sam2Runtime"


@pytest.mark.parametrize("device", ["cpu", "cuda", "cuda:0", "cuda:12", "mps"])
def test_build_lane_runtime_accepts_accelerator_devices(device: str) -> None:
    # Given: local detector and SAM2 inventory records.
    detector_entry = ModelInventoryEntry(
        "owlv2_sam2.detector", "unused/remote", "main", Path("/local/detector")
    )
    sam2_entry = ModelInventoryEntry(
        "sam2.segmenter", "unused/remote", "main", Path("/local/sam2")
    )

    # When: the runtime is built for an accelerator device.
    runtime = build_lane_runtime(
        lane=DetectorLane.OWLV2_SAM2,
        detector_entry=detector_entry,
        sam2_entry=sam2_entry,
        device=device,
    )

    # Then: construction remains configuration-only and succeeds.
    assert type(runtime).__name__ == "Owlv2Sam2Runtime"


@pytest.mark.parametrize("device", ["cuda:", "cuda:abc", "cuda:-1", "cuda:0:1"])
def test_build_lane_runtime_rejects_invalid_device(device: str) -> None:
    # Given: local detector and SAM2 inventory records.
    detector_entry = ModelInventoryEntry(
        "owlv2_sam2.detector", "unused/remote", "main", Path("/local/detector")
    )
    sam2_entry = ModelInventoryEntry(
        "sam2.segmenter", "unused/remote", "main", Path("/local/sam2")
    )

    # When / Then: only CPU, MPS, or CUDA with an optional numeric index is accepted.
    with pytest.raises(ContractValidationError, match="device"):
        _ = build_lane_runtime(
            lane=DetectorLane.OWLV2_SAM2,
            detector_entry=detector_entry,
            sam2_entry=sam2_entry,
            device=device,
        )


def test_local_loaders_use_inventory_paths_and_disable_remote_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: fake Transformers and SAM2 loader modules that record their inputs.
    sam2_calls = install_model_loader_fakes(monkeypatch)
    detector_dir = tmp_path / "detector"
    sam2_dir = tmp_path / "sam2"
    settings = LocalInferenceSettings(
        ModelInventoryEntry("detector", "unused/repo", "main", detector_dir),
        ModelInventoryEntry("sam2", "unused/repo", "main", sam2_dir),
        "mps",
        0.3,
        sam2_dir / "object-mask.png",
        (0.0, 0.0, 10.0, 8.0),
    )

    # When: each detector and the segmenter are loaded through their local seams.
    _ = load_owlv2_detector(detector_dir, "mps")
    _ = load_grounded_detector(detector_dir, "mps")
    _ = load_sam2_predictor(settings)

    # Then: every loader receives an inventory directory and local-only options.
    assert FakeLocalLoader.calls == [
        ("processor", detector_dir, {"local_files_only": True, "use_fast": False}),
        ("model", detector_dir, {"local_files_only": True}),
        ("processor", detector_dir, {"local_files_only": True}),
        ("model", detector_dir, {"local_files_only": True}),
    ]
    assert sam2_calls == [
        (Path("sam2_hiera_l.yaml"), sam2_dir / "sam2_hiera_large.pt", "mps")
    ]
