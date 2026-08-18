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
from modules.preprocessing.views.ranking_hints import compute_object_ranking_hints
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


# bbox 면적만 필요한 largest_area 사전 집계용 - 이 시점엔 아직 마스크/크롭
# 파일이 디스크에 없어(같은 루프에서 나중에 저장됨) 실제 픽셀 기반
# ranking_hints를 계산할 수 없다. object_target_with_hints와 달리 hints는
# 전부 0으로 채워 반환하지만, 호출부가 .bbox.area만 읽으므로 문제 없다.
def object_target(detection: DetectionBox) -> ObjectTarget:
    """Create bbox-only tile-planning inputs before any asset files exist."""
    return ObjectTarget(
        bbox=_bounding_box(detection),
        ranking_hints=ObjectRankingHints(
            local_texture_variance=0.0,
            local_color_variance=0.0,
            candidate_uncertainty=0.0,
            candidate_scarcity=0.0,
        ),
    )


# 실제 마스크/크롭 파일이 저장된 뒤 호출하는 버전 - 타일 우선순위(D010)에
# 쓰이는 4개 신호를 실제 픽셀에서 계산한다. write_detection_assets가 mask/
# bbox_crop을 디스크에 쓴 직후 이걸로 바꿔 호출한다.
def object_target_with_hints(
    detection: DetectionBox, bbox_crop_path: Path, mask_path: Path
) -> ObjectTarget:
    """Create tile-planning inputs with real pixel-measured ranking hints."""
    return ObjectTarget(
        bbox=_bounding_box(detection),
        ranking_hints=compute_object_ranking_hints(
            bbox_crop_path, mask_path, detection.score
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
) -> tuple[tuple[Path, BoundingBox], ...]:
    """Write all planned tile JPEGs and return each path with its own bbox.

    The bbox is in original-image coordinates (same frame as bbox_xyxy
    elsewhere) - downstream stages (rough_masking) need it to route
    detection through each tile and restore coordinates back correctly.
    Before this, the geometry tile_boxes() computed here was thrown away
    right after cropping, so nothing past this function could ever know
    where a given tile actually was.
    """
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
    return tuple(zip(paths, boxes, strict=True))
