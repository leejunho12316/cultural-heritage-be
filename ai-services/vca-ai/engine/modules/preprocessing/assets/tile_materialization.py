"""Materialize scale-aware object tile crops."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from modules.preprocessing.contracts.views import (
    BoundingBox,
    ObjectRankingHints,
    ObjectTarget,
    ViewPlanningRequest,
)
from modules.preprocessing.views.tiling import (
    target_count,
    tile_boxes,
    tile_size_and_history,
)
from modules.shared import ContractValidationError, ImageId

if TYPE_CHECKING:
    from pathlib import Path

    from PIL import Image

    from modules.preprocessing.contracts.records import DetectionBox
    from modules.preprocessing.contracts.views import ScaleMetadata
    from modules.shared import DetectorLane

TILE_MAX_SIDE_PX = 768


@dataclass(frozen=True, slots=True)
class TileGenerationInput:
    """Inputs required to write all tile crops for one object."""

    image: Image.Image
    image_id: str
    object_id: str
    run_root: Path
    largest_area: float
    request: ViewPlanningRequest


@dataclass(frozen=True, slots=True)
class TilePlanningInput:
    """Inputs required to build a dry-run-compatible tile request."""

    image: Image.Image
    image_id: str
    lane: DetectorLane
    scale_metadata: ScaleMetadata
    target: ObjectTarget


def _tile_paths(tile_root: Path, count: int) -> tuple[Path, ...]:
    return tuple(tile_root / f"tile-{index:03d}.jpg" for index in range(1, count + 1))


def _bounding_box(detection: DetectionBox) -> BoundingBox:
    return BoundingBox(
        left=detection.x0,
        top=detection.y0,
        width=detection.x1 - detection.x0,
        height=detection.y1 - detection.y0,
    )


def object_target(detection: DetectionBox) -> ObjectTarget:
    """Create deterministic tile-planning inputs for a materialized object."""
    return ObjectTarget(
        bbox=_bounding_box(detection),
        ranking_hints=ObjectRankingHints(
            local_texture_variance=0.0,
            local_color_variance=0.0,
            candidate_uncertainty=0.0,
            candidate_scarcity=0.0,
        ),
    )


def tile_request(tile_input: TilePlanningInput) -> ViewPlanningRequest:
    """Build the view-tiling request shared with dry-run planning."""
    return ViewPlanningRequest(
        image_id=ImageId(tile_input.image_id),
        image_width_px=tile_input.image.width,
        image_height_px=tile_input.image.height,
        objects=(tile_input.target,),
        scale_metadata=tile_input.scale_metadata,
        detector_lanes=(tile_input.lane,),
    )


def materialize_object_tiles(
    tile_input: TileGenerationInput,
) -> tuple[Path, ...]:
    """Write all planned tile JPEGs and return their paths."""
    tile_target = tile_input.request.objects[0]
    lane = tile_input.request.detector_lanes[0]
    target = target_count(tile_target.bbox.area, tile_input.largest_area, lane)
    tile_size, _, _ = tile_size_and_history(
        tile_input.request, tile_target, lane, target
    )
    boxes = tile_boxes(tile_target.bbox, tile_size)
    if not boxes:
        field = "tiles"
        reason = "must not be empty"
        raise ContractValidationError(field, reason)
    tile_root = (
        tile_input.run_root
        / "assets"
        / "tiles"
        / tile_input.image_id
        / tile_input.object_id
    )
    paths = _tile_paths(tile_root, len(boxes))
    for tile, path in zip(boxes, paths, strict=True):
        crop_box = (
            round(tile.left),
            round(tile.top),
            round(tile.left + tile.width),
            round(tile.top + tile.height),
        )
        tile_crop = tile_input.image.crop(crop_box)
        tile_crop.thumbnail((TILE_MAX_SIDE_PX, TILE_MAX_SIDE_PX))
        tile_crop.save(path)
    return paths
