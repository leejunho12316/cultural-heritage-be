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
from modules.preprocessing.contracts.records import DetectionBox
from modules.preprocessing.model_runtime.inventory import ModelInventoryEntry
from modules.prompt_generating import static_seed_minimal_pack


def _write_black_box_image(
    path: Path, size: tuple[int, int], boxes: tuple[tuple[int, int, int, int], ...]
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    for box in boxes:
        draw.rectangle(box, fill="black")
    image.save(path)
    return path


class FakePredictor:
    def __init__(
        self, mask: np.ndarray[tuple[int, ...], np.dtype[np.bool_]] | None = None
    ) -> None:
        self._mask: np.ndarray[tuple[int, ...], np.dtype[np.bool_]] | None
        self._mask = mask
        self.boxes: list[np.ndarray[tuple[int, ...], np.dtype[np.float64]]] = []

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
        _ = multimask_output
        self.boxes.append(box)
        mask = self._mask if self._mask is not None else np.ones((1, 1), dtype=np.bool_)
        scores = np.array([0.9], dtype=np.float32)
        logits = torch.tensor([0.0])
        return (mask,), scores, logits


def _context(
    run_root: Path, foreground_white_threshold: int = 215
) -> MaterializationContext:
    prompt = static_seed_minimal_pack.records[0]
    model_entries = {
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
    return MaterializationContext(
        run_root=run_root,
        lane=preprocessing.DetectorLane.OWLV2_SAM2,
        prompt=prompt,
        model_entries=model_entries,
        device="cpu",
        source_image_sha256="source-sha",
        detector_input_sha256="detector-sha",
        scale_metadata=preprocessing.ScaleMetadata(
            scale_marker_detected=False,
            scale_marker_bbox=None,
            scale_marker_width_px=None,
            scale_unit_px=None,
            scale_unit_source=None,
            scale_confidence=preprocessing.ScaleConfidence.UNAVAILABLE,
            confidence_reasons=("no scale marker detected",),
            fallback_reason="scale_marker_unavailable",
        ),
        scale_removal_applied=False,
        foreground_white_threshold=foreground_white_threshold,
    )


def _detection(bbox: tuple[float, float, float, float]) -> DetectionBox:
    prompt = static_seed_minimal_pack.records[0]
    return DetectionBox(
        x0=bbox[0],
        y0=bbox[1],
        x1=bbox[2],
        y1=bbox[3],
        score=0.7,
        prompt_text=prompt.prompt_text,
        generated_prompt_id=prompt.metadata.generated_prompt_id,
    )


def test_real_materialization_records_candidate_provenance_and_asset_hashes(
    tmp_path: Path,
) -> None:
    # Given: one source image, one detection, and local model provenance entries.
    run_root = tmp_path / "run"
    image = _write_black_box_image(tmp_path / "input.png", (20, 20), ((2, 2, 17, 17),))
    prompt = static_seed_minimal_pack.records[0]
    context = _context(run_root)
    detection = _detection((0.0, 0.0, 20.0, 20.0))

    # When: materialization writes the object assets.
    records = write_detection_assets(
        image,
        "image-001",
        (detection,),
        FakePredictor(),
        context,
    )

    # Then: the handoff record contains candidate, prompt, model, and asset metadata.
    record = records[0]
    assert record.accepted is True
    assert record.lane is preprocessing.DetectorLane.OWLV2_SAM2
    assert record.prompt_pack_id == prompt.metadata.prompt_pack_id
    assert record.generated_prompt_id == prompt.metadata.generated_prompt_id
    assert record.detector_model_id == "google/owlv2-base-patch16-ensemble"
    assert record.sam2_model_id == "facebook/sam2-hiera-large"
    assert record.detector_input_sha256 == "detector-sha"
    assert record.scale_removal_applied is False
    assert record.mask.sha256
    assert record.bbox_crop.media_type == "image/jpeg"
    assert record.alpha_cutout.media_type == "image/png"
    assert record.detection_overlay.media_type == "image/jpeg"
    assert Path(record.bbox_crop.path).name == "bbox_crop.jpg"
    assert Path(record.alpha_cutout.path).name == "alpha_cutout.png"
    assert Path(record.detection_overlay.path).name == "detection_overlay.jpg"
    assert Path(record.tile.path).is_file()


def test_real_materialization_splits_disconnected_mask_components(
    tmp_path: Path,
) -> None:
    # Given: one broad detection whose SAM mask contains two disconnected islands.
    run_root = tmp_path / "run"
    image = _write_black_box_image(
        tmp_path / "input.png",
        (80, 80),
        ((10, 10, 29, 29), (50, 50, 69, 69)),
    )
    mask = np.ones((80, 80), dtype=np.bool_)

    # When: materialization writes assets from the broad detection.
    records = write_detection_assets(
        image,
        "image-001",
        (_detection((0.0, 0.0, 80.0, 80.0)),),
        FakePredictor(mask),
        _context(run_root),
    )

    # Then: each connected mask island becomes a separate object record.
    assert [record.object_id for record in records] == [
        "image-001-object-01",
        "image-001-object-02",
    ]
    assert [record.bbox_xyxy for record in records] == [
        (10.0, 10.0, 30.0, 30.0),
        (50.0, 50.0, 70.0, 70.0),
    ]
    assert all(Path(record.mask.path).is_file() for record in records)


def test_real_materialization_keeps_component_pixels_outside_sam_mask(
    tmp_path: Path,
) -> None:
    # Given: a foreground component and an undersized SAM mask inside it.
    run_root = tmp_path / "run"
    image = _write_black_box_image(tmp_path / "input.png", (40, 40), ((5, 5, 34, 34),))
    sam_mask = np.zeros((40, 40), dtype=np.bool_)
    sam_mask[10:30, 10:30] = True

    # When: materialization writes assets for the object.
    records = write_detection_assets(
        image,
        "image-001",
        (_detection((0.0, 0.0, 40.0, 40.0)),),
        FakePredictor(sam_mask),
        _context(run_root),
    )

    # Then: saved mask and alpha keep component pixels even outside the SAM mask.
    mask = Image.open(records[0].mask.path).convert("L")
    alpha = Image.open(records[0].alpha_cutout.path).convert("RGBA").getchannel("A")
    assert mask.getpixel((12, 12)) == 255
    assert mask.getpixel((6, 6)) == 255
    assert alpha.getpixel((12 - 5, 12 - 5)) == 255
    assert alpha.getpixel((6 - 5, 6 - 5)) == 255
    assert records[0].bbox_xyxy == (5.0, 5.0, 35.0, 35.0)


def test_real_materialization_scales_sam_mask_from_thumbnail_coordinates(
    tmp_path: Path,
) -> None:
    # Given: a large source image that is downscaled before SAM prediction.
    run_root = tmp_path / "run"
    image = _write_black_box_image(
        tmp_path / "input.png", (2048, 2048), ((200, 200, 1799, 1799),)
    )
    predictor = FakePredictor(np.ones((1024, 1024), dtype=np.bool_))

    # When: materialization predicts a SAM mask from the thumbnail image.
    records = write_detection_assets(
        image,
        "image-001",
        (_detection((200.0, 200.0, 1800.0, 1800.0)),),
        predictor,
        _context(run_root),
    )

    # Then: the SAM box is scaled while the saved mask remains in source coordinates.
    assert predictor.boxes[0].tolist() == [100.0, 100.0, 900.0, 900.0]
    mask = Image.open(records[0].mask.path).convert("L")
    assert mask.size == (2048, 2048)
    assert mask.getpixel((250, 250)) == 255


def test_real_materialization_foreground_threshold_tightens_near_white_pixels(
    tmp_path: Path,
) -> None:
    # Given: a near-white region that only the looser threshold treats as foreground.
    image_path = tmp_path / "input.png"
    image = Image.new("RGB", (40, 40), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((5, 5, 34, 34), fill=(242, 242, 242))
    image.save(image_path)

    # When: materialization uses the tightened foreground threshold.
    tight_records = write_detection_assets(
        image_path,
        "image-001",
        (_detection((0.0, 0.0, 40.0, 40.0)),),
        FakePredictor(),
        _context(tmp_path / "tight", foreground_white_threshold=240),
    )
    loose_records = write_detection_assets(
        image_path,
        "image-001",
        (_detection((0.0, 0.0, 40.0, 40.0)),),
        FakePredictor(),
        _context(tmp_path / "loose", foreground_white_threshold=245),
    )

    # Then: the tightened threshold excludes the near-white region.
    assert tight_records == ()
    assert len(loose_records) == 1


def test_real_materialization_preserves_real_white_hole_in_object(
    tmp_path: Path,
) -> None:
    # Given: one dark object with a real white interior hole.
    image_path = tmp_path / "input.png"
    image = Image.new("RGB", (40, 40), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((5, 5, 34, 34), fill="black")
    draw.rectangle((17, 17, 22, 22), fill="white")
    image.save(image_path)

    # When: materialization writes foreground-component assets.
    records = write_detection_assets(
        image_path,
        "image-001",
        (_detection((0.0, 0.0, 40.0, 40.0)),),
        FakePredictor(),
        _context(tmp_path / "run"),
    )

    # Then: the real hole remains transparent while object pixels stay opaque.
    mask = Image.open(records[0].mask.path).convert("L")
    alpha = Image.open(records[0].alpha_cutout.path).convert("RGBA").getchannel("A")
    assert mask.getpixel((10, 10)) == 255
    assert mask.getpixel((19, 19)) == 0
    assert alpha.getpixel((10 - 5, 10 - 5)) == 255
    assert alpha.getpixel((19 - 5, 19 - 5)) == 0
