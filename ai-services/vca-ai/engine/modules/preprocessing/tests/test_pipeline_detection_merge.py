from __future__ import annotations

from struct import pack
from typing import TYPE_CHECKING
from zlib import compress, crc32

from modules import preprocessing
from modules.preprocessing.contracts.records import DetectionBox
from modules.preprocessing.contracts.views import (
    BoundingBox,
    ScaleConfidence,
    ScaleMetadata,
)
from modules.preprocessing.detection.scale_marker import ScaleRemovalResult
from modules.preprocessing.pipeline import run

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from modules.preprocessing.assets.materialization import MaterializationContext
    from modules.preprocessing.contracts.records import (
        DetectionRun,
        ObjectAssetRecord,
    )
    from modules.preprocessing.model_runtime.inventory import ModelInventoryEntry
    from modules.preprocessing.model_runtime.options import RealPreprocessingOptions


def _chunk(name: bytes, payload: bytes) -> bytes:
    return (
        pack(">I", len(payload))
        + name
        + payload
        + pack(">I", crc32(name + payload) & 0xFFFFFFFF)
    )


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    + _chunk(b"IHDR", pack(">IIBBBBB", 100, 100, 8, 6, 0, 0, 0))
    + _chunk(b"IDAT", compress(b"\x00" * 40_100))
    + _chunk(b"IEND", b"")
)


class FakeProcessor:
    pass


class FakeModel:
    pass


class FakePredictor:
    pass


def _write_image(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(PNG_BYTES)
    return path


def test_real_pipeline_filters_broad_and_materializes_merged_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a 100 by 100 pipeline input with broad and adjacent raw detections.
    workspace = tmp_path / "workspace"
    image = _write_image(workspace / "inputs" / "artifact.png")
    run_root = workspace / "runs" / "merged"
    materialized_detections: list[tuple[DetectionBox, ...]] = []
    detector_input_sizes: list[int] = []
    monkeypatch.chdir(workspace)

    def load_inventory(
        options: RealPreprocessingOptions,
    ) -> dict[str, ModelInventoryEntry]:
        _ = options
        return {}

    def validate_cache(entries: dict[str, ModelInventoryEntry]) -> None:
        _ = entries

    def resolve_mps(device: preprocessing.Device) -> str:
        _ = device
        return "mps"

    def load_models(
        entries: dict[str, ModelInventoryEntry], device: str
    ) -> tuple[FakeProcessor, FakeModel, FakePredictor]:
        _ = entries, device
        return FakeProcessor(), FakeModel(), FakePredictor()

    def raw_adjacent_detections(
        image_path: Path,
        detection_run: DetectionRun[FakeProcessor, FakeModel],
    ) -> tuple[DetectionBox, ...]:
        _ = image_path
        detector_input_sizes.append(detection_run.detector_input_size)
        return (
            DetectionBox(1.0, 2.0, 99.0, 97.0, 0.95, "artifact", "prompt-a"),
            DetectionBox(10.0, 10.0, 30.0, 30.0, 0.6, "artifact", "prompt-a"),
            DetectionBox(46.0, 25.0, 66.0, 45.0, 0.9, "artifact", "prompt-a"),
        )

    def capture_materialization(
        image_path: Path,
        image_id: str,
        detections: tuple[DetectionBox, ...],
        predictor: FakePredictor,
        context: MaterializationContext,
    ) -> tuple[ObjectAssetRecord, ...]:
        _ = image_path, image_id, predictor, context
        materialized_detections.append(detections)
        return ()

    monkeypatch.setattr(
        "modules.preprocessing.pipeline.resolve_runtime_device", resolve_mps
    )
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.load_model_inventory", load_inventory
    )
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.validate_model_cache", validate_cache
    )
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.load_runtime_models", load_models
    )
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.run_runtime_detection", raw_adjacent_detections
    )
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.write_detection_assets", capture_materialization
    )

    # When: the real pipeline reaches its materialization boundary.
    exit_code = run(
        (
            str(image.relative_to(workspace)),
            "--run-root",
            str(run_root.relative_to(workspace)),
            "--device",
            "mps",
            "--detector-lane",
            "owlv2_sam2",
            "--merge-strategy",
            "union",
        )
    )

    # Then: materialization receives the broad container for SAM component splitting.
    assert exit_code == preprocessing.ExitCode.OK
    assert materialized_detections == [
        (DetectionBox(1.0, 2.0, 99.0, 97.0, 0.95, "artifact", "prompt-a"),)
    ]
    assert detector_input_sizes == [960]


def test_real_pipeline_keeps_scale_marker_overlapping_candidate_before_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: preprocessing reports a scale marker and runtime returns a covering box.
    workspace = tmp_path / "workspace"
    image = _write_image(workspace / "inputs" / "artifact.png")
    run_root = workspace / "runs" / "scale-filtered"
    materialized_detections: list[tuple[DetectionBox, ...]] = []
    monkeypatch.chdir(workspace)

    def load_inventory(
        options: RealPreprocessingOptions,
    ) -> dict[str, ModelInventoryEntry]:
        _ = options
        return {}

    def validate_cache(entries: dict[str, ModelInventoryEntry]) -> None:
        _ = entries

    def resolve_mps(device: preprocessing.Device) -> str:
        _ = device
        return "mps"

    def load_models(
        entries: dict[str, ModelInventoryEntry], device: str
    ) -> tuple[FakeProcessor, FakeModel, FakePredictor]:
        _ = entries, device
        return FakeProcessor(), FakeModel(), FakePredictor()

    def scale_overlapping_detections(
        image_path: Path,
        detection_run: DetectionRun[FakeProcessor, FakeModel],
    ) -> tuple[DetectionBox, ...]:
        _ = image_path, detection_run
        return (
            DetectionBox(0.0, 0.0, 100.0, 100.0, 0.8, "artifact", "prompt-a"),
            DetectionBox(10.0, 10.0, 30.0, 30.0, 0.7, "artifact", "prompt-a"),
        )

    def scale_preprocessing(
        image_path: Path,
        run_root: Path,
        image_id: str,
        options: object,
    ) -> ScaleRemovalResult:
        _ = options
        target = run_root / "assets" / "detector_inputs" / f"{image_id}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_bytes(image_path.read_bytes())
        bbox = BoundingBox(left=80.0, top=90.0, width=15.0, height=5.0)
        return ScaleRemovalResult(
            detector_input_path=target,
            detector_input_sha256="sha",
            scale_metadata=ScaleMetadata(
                scale_marker_detected=True,
                scale_marker_bbox=bbox,
                scale_marker_width_px=15.0,
                scale_unit_px=5.0,
                scale_unit_source="test",
                scale_confidence=ScaleConfidence.HIGH,
                confidence_reasons=("test",),
                fallback_reason=None,
            ),
            scale_removal_applied=True,
            scale_removal_mode="test",
            scale_removal_bbox=bbox,
        )

    def capture_materialization(
        image_path: Path,
        image_id: str,
        detections: tuple[DetectionBox, ...],
        predictor: FakePredictor,
        context: MaterializationContext,
    ) -> tuple[ObjectAssetRecord, ...]:
        _ = image_path, image_id, predictor, context
        materialized_detections.append(detections)
        return ()

    monkeypatch.setattr(
        "modules.preprocessing.pipeline.resolve_runtime_device", resolve_mps
    )
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.load_model_inventory", load_inventory
    )
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.validate_model_cache", validate_cache
    )
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.load_runtime_models", load_models
    )
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.run_runtime_detection",
        scale_overlapping_detections,
    )
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.prepare_detector_input", scale_preprocessing
    )
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.write_detection_assets", capture_materialization
    )

    # When: the pipeline reaches its materialization boundary.
    exit_code = run(
        (
            str(image.relative_to(workspace)),
            "--run-root",
            str(run_root.relative_to(workspace)),
            "--device",
            "mps",
            "--detector-lane",
            "owlv2_sam2",
            "--scale-marker-coverage-ratio",
            "0.8",
        )
    )

    # Then: materialization receives the broad object container for component splitting.
    assert exit_code == preprocessing.ExitCode.OK
    assert materialized_detections == [
        (DetectionBox(0.0, 0.0, 100.0, 100.0, 0.8, "artifact", "prompt-a"),)
    ]


def test_real_pipeline_retries_lower_threshold_then_uses_foreground_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: OWLv2 returns no object at either the default or relaxed threshold.
    workspace = tmp_path / "workspace"
    image = _write_image(workspace / "inputs" / "artifact.png")
    run_root = workspace / "runs" / "fallback"
    thresholds: list[float] = []
    materialized_detections: list[tuple[DetectionBox, ...]] = []
    monkeypatch.chdir(workspace)

    def load_inventory(
        options: RealPreprocessingOptions,
    ) -> dict[str, ModelInventoryEntry]:
        _ = options
        return {}

    def validate_cache(entries: dict[str, ModelInventoryEntry]) -> None:
        _ = entries

    def resolve_mps(device: preprocessing.Device) -> str:
        _ = device
        return "mps"

    def load_models(
        entries: dict[str, ModelInventoryEntry], device: str
    ) -> tuple[FakeProcessor, FakeModel, FakePredictor]:
        _ = entries, device
        return FakeProcessor(), FakeModel(), FakePredictor()

    def no_detections(
        image_path: Path,
        detection_run: DetectionRun[FakeProcessor, FakeModel],
    ) -> tuple[DetectionBox, ...]:
        _ = image_path
        thresholds.append(detection_run.threshold)
        return ()

    def capture_materialization(
        image_path: Path,
        image_id: str,
        detections: tuple[DetectionBox, ...],
        predictor: FakePredictor,
        context: MaterializationContext,
    ) -> tuple[ObjectAssetRecord, ...]:
        _ = image_path, image_id, predictor, context
        materialized_detections.append(detections)
        return ()

    monkeypatch.setattr(
        "modules.preprocessing.pipeline.resolve_runtime_device", resolve_mps
    )
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.load_model_inventory", load_inventory
    )
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.validate_model_cache", validate_cache
    )
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.load_runtime_models", load_models
    )
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.run_runtime_detection", no_detections
    )
    monkeypatch.setattr(
        "modules.preprocessing.pipeline.write_detection_assets", capture_materialization
    )

    # When: real preprocessing runs with no detector candidate.
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

    # Then: it retries at 0.075 and still supplies a deterministic fallback bbox.
    assert exit_code == preprocessing.ExitCode.OK
    assert thresholds == [0.15, 0.075]
    assert len(materialized_detections) == 1
    assert len(materialized_detections[0]) == 1
    assert materialized_detections[0][0].prompt_text == "artifact foreground fallback"
