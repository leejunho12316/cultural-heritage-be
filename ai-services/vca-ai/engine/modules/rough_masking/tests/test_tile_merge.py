from __future__ import annotations

import json
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

from modules.rough_masking.tile_merge import merge_tile_split_rough_candidates

if TYPE_CHECKING:
    from pathlib import Path

_PROMPT = "surface crack"
_LANE = "owlv2_sam2"


def _write_view(  # noqa: PLR0913
    root: Path,
    object_id: str,
    view_segment: str,
    *,
    bbox_xyxy: tuple[float, float, float, float],
    prompt: str = _PROMPT,
    lane: str = _LANE,
    image: str = "image-1",
    score: float = 0.5,
) -> Path:
    view_dir = root / lane / object_id / lane / view_segment
    view_dir.mkdir(parents=True, exist_ok=True)
    mask = np.zeros((200, 200), dtype=np.uint8)
    x0, y0, x1, y1 = (round(value) for value in bbox_xyxy)
    mask[y0:y1, x0:x1] = 255
    (view_dir / "masks").mkdir(exist_ok=True)
    (view_dir / "overlays").mkdir(exist_ok=True)
    Image.fromarray(mask).save(view_dir / "masks" / "anomaly-0000.png")
    Image.fromarray(mask).save(view_dir / "overlays" / "anomaly-0000.jpg")
    record = {
        "accepted": True,
        "prompt": prompt,
        "image": image,
        "generation_lane": "owlv2",
        "prompt_pack_id": "static-seed-minimal-v1",
        "score": score,
        "bbox_xyxy": list(bbox_xyxy),
        "mask_semantics": "anomaly_region",
        "mask_path": "masks/anomaly-0000.png",
        "overlay_path": "overlays/anomaly-0000.jpg",
    }
    records_path = view_dir / "records.json"
    records_path.write_text(json.dumps([record]), encoding="utf-8")
    return records_path


def _rows(records_path: Path) -> list[dict[str, object]]:
    return json.loads(records_path.read_text(encoding="utf-8"))


def test_merges_overlapping_candidates_from_different_tiles(tmp_path: Path) -> None:
    # Given: two candidates from different tiles of the same object, same
    # seed prompt, with overlapping bboxes (as tiling's 0.33 overlap ratio
    # would produce for a single physical anomaly split across a boundary).
    left = _write_view(
        tmp_path, "object-1", "tile-a", bbox_xyxy=(0.0, 0.0, 60.0, 60.0)
    )
    right = _write_view(
        tmp_path, "object-1", "tile-b", bbox_xyxy=(40.0, 40.0, 100.0, 100.0)
    )

    # When: tile merge runs.
    merge_tile_split_rough_candidates(tmp_path)

    # Then: both original rows are suppressed, and exactly one new merged
    # accepted row exists whose bbox spans both fragments.
    assert _rows(left)[0]["accepted"] is False
    assert _rows(left)[0]["reject_reason"] == "merged_into_tile_group"
    assert _rows(right)[0]["accepted"] is False
    merged_path = (
        tmp_path / _LANE / "object-1" / _LANE / "tile_merged" / "records.json"
    )
    merged_rows = _rows(merged_path)
    assert len(merged_rows) == 1
    assert merged_rows[0]["accepted"] is True
    assert merged_rows[0]["bbox_xyxy"] == [0.0, 0.0, 100.0, 100.0]
    assert merged_rows[0]["prompt"] == _PROMPT


def test_does_not_merge_candidates_from_the_same_tile(tmp_path: Path) -> None:
    # Given: two overlapping candidates from the *same* tile - this is
    # relations.py's job downstream (real concept evidence to judge with),
    # not a tile-boundary artifact.
    view_dir = tmp_path / _LANE / "object-1" / _LANE / "tile-a"
    view_dir.mkdir(parents=True)
    mask = np.zeros((200, 200), dtype=np.uint8)
    (view_dir / "masks").mkdir()
    (view_dir / "overlays").mkdir()
    mask_one = mask.copy()
    mask_one[0:60, 0:60] = 255
    mask_two = mask.copy()
    mask_two[40:100, 40:100] = 255
    Image.fromarray(mask_one).save(view_dir / "masks" / "one.png")
    Image.fromarray(mask_two).save(view_dir / "masks" / "two.png")
    Image.fromarray(mask).save(view_dir / "overlays" / "one.jpg")
    Image.fromarray(mask).save(view_dir / "overlays" / "two.jpg")
    records = [
        {
            "accepted": True,
            "prompt": _PROMPT,
            "image": "image-1",
            "generation_lane": "owlv2",
            "prompt_pack_id": "static-seed-minimal-v1",
            "score": 0.5,
            "bbox_xyxy": [0.0, 0.0, 60.0, 60.0],
            "mask_semantics": "anomaly_region",
            "mask_path": "masks/one.png",
            "overlay_path": "overlays/one.jpg",
        },
        {
            "accepted": True,
            "prompt": _PROMPT,
            "image": "image-1",
            "generation_lane": "owlv2",
            "prompt_pack_id": "static-seed-minimal-v1",
            "score": 0.5,
            "bbox_xyxy": [40.0, 40.0, 100.0, 100.0],
            "mask_semantics": "anomaly_region",
            "mask_path": "masks/two.png",
            "overlay_path": "overlays/two.jpg",
        },
    ]
    records_path = view_dir / "records.json"
    records_path.write_text(json.dumps(records), encoding="utf-8")

    # When: tile merge runs.
    merge_tile_split_rough_candidates(tmp_path)

    # Then: both rows remain accepted (untouched) and no tile_merged output
    # is written for this object.
    rows = _rows(records_path)
    assert all(row["accepted"] is True for row in rows)
    assert not (
        tmp_path / _LANE / "object-1" / _LANE / "tile_merged" / "records.json"
    ).exists()


def test_does_not_merge_candidates_with_different_seed_prompts(tmp_path: Path) -> None:
    # Given: two overlapping candidates from different tiles of the same
    # object, but caught by different seed prompts - unlike the removed
    # pre_rag.py, this narrower revival requires the same seed prompt too.
    left = _write_view(
        tmp_path,
        "object-1",
        "tile-a",
        bbox_xyxy=(0.0, 0.0, 60.0, 60.0),
        prompt="surface crack",
    )
    right = _write_view(
        tmp_path,
        "object-1",
        "tile-b",
        bbox_xyxy=(40.0, 40.0, 100.0, 100.0),
        prompt="corrosion",
    )

    # When: tile merge runs.
    merge_tile_split_rough_candidates(tmp_path)

    # Then: both pass through unmerged.
    assert _rows(left)[0]["accepted"] is True
    assert _rows(right)[0]["accepted"] is True


def test_does_not_merge_candidates_without_a_tile_origin(tmp_path: Path) -> None:
    # Given: two overlapping candidates that both come from the whole-object
    # crop view ("object" segment, no tile origin) - merging these on
    # overlap alone would resurrect the old pre_rag.py's over-merging bug.
    left = _write_view(
        tmp_path, "object-1", "object", bbox_xyxy=(0.0, 0.0, 60.0, 60.0)
    )
    right = _write_view(
        tmp_path, "object-1", "tile-a", bbox_xyxy=(40.0, 40.0, 100.0, 100.0)
    )

    # When: tile merge runs.
    merge_tile_split_rough_candidates(tmp_path)

    # Then: the object-crop candidate never merges, even with a genuinely
    # overlapping same-prompt tile candidate.
    assert _rows(left)[0]["accepted"] is True
    assert _rows(right)[0]["accepted"] is True


def test_does_not_merge_non_overlapping_tiles(tmp_path: Path) -> None:
    # Given: two candidates from different tiles of the same object whose
    # bboxes do not actually overlap.
    left = _write_view(
        tmp_path, "object-1", "tile-a", bbox_xyxy=(0.0, 0.0, 20.0, 20.0)
    )
    right = _write_view(
        tmp_path, "object-1", "tile-b", bbox_xyxy=(150.0, 150.0, 170.0, 170.0)
    )

    # When: tile merge runs.
    merge_tile_split_rough_candidates(tmp_path)

    # Then: both pass through unmerged.
    assert _rows(left)[0]["accepted"] is True
    assert _rows(right)[0]["accepted"] is True


def test_does_not_merge_across_different_objects(tmp_path: Path) -> None:
    # Given: two overlapping, different-tile, same-prompt candidates that
    # belong to two different physical objects.
    left = _write_view(
        tmp_path, "object-a", "tile-1", bbox_xyxy=(0.0, 0.0, 60.0, 60.0)
    )
    right = _write_view(
        tmp_path, "object-b", "tile-2", bbox_xyxy=(40.0, 40.0, 100.0, 100.0)
    )

    # When: tile merge runs.
    merge_tile_split_rough_candidates(tmp_path)

    # Then: both pass through unmerged.
    assert _rows(left)[0]["accepted"] is True
    assert _rows(right)[0]["accepted"] is True


def test_merged_output_is_readable_by_both_downstream_readers(tmp_path: Path) -> None:
    # Given: two overlapping same-prompt candidates from different tiles.
    _write_view(tmp_path, "object-1", "tile-a", bbox_xyxy=(0.0, 0.0, 60.0, 60.0))
    _write_view(
        tmp_path, "object-1", "tile-b", bbox_xyxy=(40.0, 40.0, 100.0, 100.0)
    )

    # When: tile merge runs, and both real downstream readers parse the
    # resulting project tree.
    merge_tile_split_rough_candidates(tmp_path)

    from modules.rag.operations.candidate_sidecar_artifacts import (  # noqa: PLC0415
        read_rough_records,
    )
    from modules.visual_cue_generation.rough_records import (  # noqa: PLC0415
        rough_qwen_candidates,
    )

    rag_candidates = read_rough_records(tmp_path)
    qwen_candidates = rough_qwen_candidates(tmp_path, tmp_path)

    # Then: exactly one candidate survives project-wide in both readers, and
    # it carries source_tile_view_id=None (already merged, no longer
    # single-tile-scoped).
    assert len(rag_candidates) == 1
    assert rag_candidates[0].candidate_id.startswith("tile-merged:")
    assert len(qwen_candidates) == 1
    assert qwen_candidates[0].candidate.source_tile_view_id is None
    assert qwen_candidates[0].candidate.bbox_xyxy == (0.0, 0.0, 100.0, 100.0)


def _write_tile_local_view(  # noqa: PLR0913
    root: Path,
    object_id: str,
    view_segment: str,
    *,
    bbox_xyxy: tuple[float, float, float, float],
    mask_shape: tuple[int, int],
    prompt: str = _PROMPT,
    lane: str = _LANE,
    image: str = "image-1",
) -> Path:
    """Write a view whose mask is sized to its own tile crop, not a shared canvas.

    Mirrors what real rough_masking tiles actually produce: each tile's
    mask.png is exactly that tile's own crop size (subject to float-rounding
    drift between tiles), fully foreground, in tile-local pixel space - never
    a fixed canvas already aligned to the source image.
    """
    view_dir = root / lane / object_id / lane / view_segment
    view_dir.mkdir(parents=True, exist_ok=True)
    mask = np.full(mask_shape, 255, dtype=np.uint8)
    (view_dir / "masks").mkdir(exist_ok=True)
    (view_dir / "overlays").mkdir(exist_ok=True)
    Image.fromarray(mask).save(view_dir / "masks" / "anomaly-0000.png")
    Image.fromarray(mask).save(view_dir / "overlays" / "anomaly-0000.jpg")
    record = {
        "accepted": True,
        "prompt": prompt,
        "image": image,
        "generation_lane": "owlv2",
        "prompt_pack_id": "static-seed-minimal-v1",
        "score": 0.5,
        "bbox_xyxy": list(bbox_xyxy),
        "mask_semantics": "anomaly_region",
        "mask_path": "masks/anomaly-0000.png",
        "overlay_path": "overlays/anomaly-0000.jpg",
    }
    records_path = view_dir / "records.json"
    records_path.write_text(json.dumps([record]), encoding="utf-8")
    return records_path


def test_merges_tile_local_masks_of_mismatched_pixel_size(tmp_path: Path) -> None:
    # Given: two tiles whose masks are each sized to their own crop (as real
    # rough_masking tiles are) and differ by one pixel due to float-rounding
    # drift between tile boundaries - reproducing a real failure where a
    # 373x373 and a 373x372 tile mask could not be unioned directly.
    left = _write_tile_local_view(
        tmp_path,
        "object-1",
        "tile-a",
        bbox_xyxy=(0.0, 0.0, 60.0, 60.0),
        mask_shape=(60, 60),
    )
    right = _write_tile_local_view(
        tmp_path,
        "object-1",
        "tile-b",
        bbox_xyxy=(40.0, 40.0, 100.0, 99.0),
        mask_shape=(59, 60),
    )

    # When: tile merge runs.
    merge_tile_split_rough_candidates(tmp_path)

    # Then: it does not crash on the shape mismatch, and the merged bbox
    # spans the true union of both tiles' *image-coordinate* boxes - not a
    # bbox re-derived from raw pixel positions in some tile's local frame.
    assert _rows(left)[0]["accepted"] is False
    assert _rows(right)[0]["accepted"] is False
    merged_path = (
        tmp_path / _LANE / "object-1" / _LANE / "tile_merged" / "records.json"
    )
    merged_rows = _rows(merged_path)
    assert len(merged_rows) == 1
    assert merged_rows[0]["bbox_xyxy"] == [0.0, 0.0, 100.0, 99.0]

    # And: the merged mask is a real image-sized canvas (100x99) with both
    # tiles' foreground correctly placed at their own offsets, not a 60x60 or
    # 59x60 array silently truncated to one tile's shape.
    mask_filename = merged_rows[0]["mask_path"].split("/")[-1]
    merged_mask = np.asarray(
        Image.open(merged_path.parent / "masks" / mask_filename)
    )
    assert merged_mask.shape == (99, 100)
    assert merged_mask[10, 10] > 0  # inside left tile's placed region
    assert merged_mask[90, 90] > 0  # inside right tile's placed region
