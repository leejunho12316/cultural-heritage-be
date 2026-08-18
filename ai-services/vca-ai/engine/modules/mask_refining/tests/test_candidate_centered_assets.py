from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING

from PIL import Image

from modules.mask_refining.execution.assets import (
    candidate_centered_assets,
    join_preprocessing_assets,
)
from modules.rough_masking.artifacts.assets import AssetReference
from modules.rough_masking.candidates import CandidateStatus, RawDetectorCandidate
from modules.rough_masking.contracts import SeedThresholds
from modules.shared import (
    CandidateId,
    DetectorLane,
    ImageId,
    PromptMetadata,
    PromptRole,
    RagLane,
)

if TYPE_CHECKING:
    from pathlib import Path

_OBJECT_BBOX = (100.0, 100.0, 300.0, 300.0)  # 200x200 object crop


def _write_preprocessing_assets(root: Path) -> None:
    object_root = root / "assets" / "objects" / "object-001"
    object_root.mkdir(parents=True)
    Image.new("RGB", (200, 200), (128, 128, 128)).save(
        object_root / "bbox_crop.jpg", format="JPEG"
    )
    Image.new("L", (200, 200), 255).save(object_root / "mask.png", format="PNG")
    manifest = {
        "objects": [
            {
                "object_id": "object-001",
                "image_id": "image-001",
                "bbox_xyxy": list(_OBJECT_BBOX),
                "bbox_crop": {"path": str(object_root / "bbox_crop.jpg")},
                "mask": {"path": str(object_root / "mask.png")},
            }
        ]
    }
    manifests = root / "manifests"
    manifests.mkdir()
    _ = (manifests / "real_preprocessing_manifest.json").write_text(
        json.dumps(manifest)
    )


def _rough_candidate(
    view_origin_xyxy: tuple[float, float, float, float],
) -> RawDetectorCandidate:
    metadata = PromptMetadata(
        "static-seed", PromptRole.STATIC_SEED, RagLane.OWLV2, "seed-001", ("mark",)
    )
    return RawDetectorCandidate(
        CandidateId("rough-parent-001"),
        ImageId("image-001"),
        DetectorLane.OWLV2_SAM2,
        CandidateStatus.ACCEPTED,
        "mark",
        0.9,
        (90.0, 90.0, 110.0, 110.0),
        AssetReference("mask.png", "a" * 64, "image/png"),
        AssetReference("overlay.jpg", "b" * 64, "image/jpeg"),
        "detector",
        "sam2",
        SeedThresholds(0.08, None, 2, 0.3),
        metadata,
        "source-001",
        "object-001",
        None,
        (),
        view_origin_xyxy,
        None,
    )


def test_candidate_centered_crop_is_narrower_than_the_whole_object(
    tmp_path: Path,
) -> None:
    # Given: a 200x200 object crop and a candidate whose own tight bbox
    # (restored to original-photo space) is only 20x20, deep inside the
    # object - exactly the shape of a real anomaly on a large object.
    _write_preprocessing_assets(tmp_path)
    assets = join_preprocessing_assets(tmp_path, "object-001", "image-001")
    assert assets is not None
    candidate = _rough_candidate(_OBJECT_BBOX)

    # When: the ROI is narrowed to a candidate-centered crop.
    result = candidate_centered_assets(candidate, assets, tmp_path / "out", tmp_path)

    # Then: the new ROI is strictly smaller than the whole-object crop, and
    # the padded region is centered on the candidate (bbox-size-proportional
    # padding of 20px floored at the 32px minimum -> padded to [158,158,242,242]).
    assert result.roi_image_path != assets.roi_image_path
    assert result.roi_image_path.is_file()
    assert (result.image_width_px, result.image_height_px) == (84, 84)
    transform = result.view.coordinate_transform
    assert transform is not None
    assert (transform.restore_offset_x, transform.restore_offset_y) == (158.0, 158.0)
    assert transform.source_bbox.width == 84.0
    assert transform.source_bbox.height == 84.0
    with Image.open(result.roi_image_path) as crop:
        assert crop.size == (84, 84)

    # Then: object-level fields the crop doesn't own stay untouched.
    assert result.object_mask_path == assets.object_mask_path
    assert result.original_image_width_px == assets.original_image_width_px
    assert result.original_image_height_px == assets.original_image_height_px


def test_candidate_centered_crop_clamps_to_the_object_bounds_near_an_edge(
    tmp_path: Path,
) -> None:
    # Given: a candidate whose tight bbox sits right at the object's own
    # top-left corner, so full padding would reach past the object crop.
    _write_preprocessing_assets(tmp_path)
    assets = join_preprocessing_assets(tmp_path, "object-001", "image-001")
    assert assets is not None
    candidate = replace(
        _rough_candidate(_OBJECT_BBOX), bbox_xyxy=(0.0, 0.0, 10.0, 10.0)
    )

    # When: the ROI is narrowed to a candidate-centered crop.
    result = candidate_centered_assets(candidate, assets, tmp_path / "out", tmp_path)

    # Then: the padded crop is clamped to the object's own bounds rather than
    # reaching into pixels the object crop file doesn't have.
    transform = result.view.coordinate_transform
    assert transform is not None
    assert transform.restore_offset_x == 100.0
    assert transform.restore_offset_y == 100.0


def test_candidate_centered_assets_reused_by_qwen_stay_self_consistent(
    tmp_path: Path,
) -> None:
    # Given: the same setup as the narrowing test above.
    _write_preprocessing_assets(tmp_path)
    assets = join_preprocessing_assets(tmp_path, "object-001", "image-001")
    assert assets is not None
    candidate = _rough_candidate(_OBJECT_BBOX)

    # When: the ROI is narrowed to a candidate-centered crop.
    result = candidate_centered_assets(candidate, assets, tmp_path / "out", tmp_path)

    # Then: the crop's own pixel dimensions match what a re-detection run
    # (and Qwen's post-refinement renderer, which reuses the same
    # roi_image_path/image_width_px/image_height_px) would see.
    with Image.open(result.roi_image_path) as crop:
        assert crop.size == (result.image_width_px, result.image_height_px)
