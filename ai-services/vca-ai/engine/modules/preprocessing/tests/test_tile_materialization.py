from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw

from modules import preprocessing
from modules.preprocessing.assets.materialization import (
    MaterializationContext,
    write_detection_assets,
)
from modules.preprocessing.contracts.records import (
    DetectionBox,
    RealPreprocessingManifest,
)
from modules.preprocessing.model_runtime.inventory import ModelInventoryEntry
from modules.prompt_generating import static_seed_minimal_pack


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
        return (
            (np.ones((1, 1), dtype=np.bool_),),
            np.array([0.9], dtype=np.float32),
            torch.tensor([0.0]),
        )


def _write_large_object_image(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (2000, 1400), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 100, 1899, 1299), fill="black")
    image.save(path)
    return path


def _scale_metadata() -> preprocessing.ScaleMetadata:
    return preprocessing.ScaleMetadata(
        scale_marker_detected=True,
        scale_marker_bbox=preprocessing.BoundingBox(20.0, 20.0, 300.0, 20.0),
        scale_marker_width_px=300.0,
        scale_unit_px=300.0,
        scale_unit_source="test",
        scale_confidence=preprocessing.ScaleConfidence.HIGH,
        confidence_reasons=("test scale",),
        fallback_reason=None,
    )


def _context(run_root: Path) -> MaterializationContext:
    prompt = static_seed_minimal_pack.records[0]
    return MaterializationContext(
        run_root=run_root,
        lane=preprocessing.DetectorLane.OWLV2_SAM2,
        prompt=prompt,
        model_entries={
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
        },
        device="cpu",
        source_image_sha256="source-sha",
        detector_input_sha256="detector-sha",
        scale_metadata=_scale_metadata(),
        scale_removal_applied=False,
        foreground_white_threshold=215,
    )


def _detection() -> DetectionBox:
    prompt = static_seed_minimal_pack.records[0]
    return DetectionBox(
        x0=100.0,
        y0=100.0,
        x1=1900.0,
        y1=1300.0,
        score=0.7,
        prompt_text=prompt.prompt_text,
        generated_prompt_id=prompt.metadata.generated_prompt_id,
    )


def test_real_materialization_writes_all_planned_tiles(tmp_path: Path) -> None:
    # Given: one large scaled object whose view geometry requires multiple tiles.
    image = _write_large_object_image(tmp_path / "input.png")

    # When: materialization writes assets for the object.
    records = write_detection_assets(
        image,
        "image-001",
        (_detection(),),
        FakePredictor(),
        _context(tmp_path / "run"),
    )

    # Then: every planned tile is materialized with deterministic names and assets.
    record = records[0]
    assert len(record.tiles) > 1
    assert record.tile == record.tiles[0]
    assert [Path(tile.path).name for tile in record.tiles] == [
        f"tile-{index:03d}.jpg" for index in range(1, len(record.tiles) + 1)
    ]
    for tile in record.tiles:
        assert tile.sha256
        assert tile.media_type == "image/jpeg"
        with Image.open(tile.path) as tile_image:
            assert tile_image.width <= 768
            assert tile_image.height <= 768


def test_real_manifest_keeps_legacy_tile_and_adds_all_tiles(tmp_path: Path) -> None:
    # Given: a materialized object with multiple tiles.
    record = write_detection_assets(
        _write_large_object_image(tmp_path / "input.png"),
        "image-001",
        (_detection(),),
        FakePredictor(),
        _context(tmp_path / "run"),
    )[0]
    manifest = RealPreprocessingManifest(
        schema_version="vca-real-preprocessing-v2",
        model_inventory="models/inventory/model_inventory.json",
        device="cpu",
        detector_lane_status="real_executed",
        model_invocations=1,
        sam2_calls=1,
        manifest_id="manifest-001",
        processed_image_count=1,
        object_count=1,
        images=(),
        objects=(record,),
    )

    # When: the manifest is converted into the public JSON-compatible shape.
    jsonable = manifest.to_jsonable()
    objects_value = jsonable["objects"]
    assert isinstance(objects_value, list)
    object_record = objects_value[0]
    tiles_value = object_record["tiles"]
    assert isinstance(tiles_value, tuple)

    # Then: the legacy tile field remains and the additive tiles field lists all tiles.
    assert jsonable["schema_version"] == "vca-real-preprocessing-v2"
    assert jsonable["tile_count"] == len(record.tiles)
    assert jsonable["tile_budget_limit"] == 1000
    assert jsonable["requires_user_budget_approval"] is False
    assert "tile" in object_record
    assert "tiles" in object_record
    assert object_record["tile"] == tiles_value[0]
