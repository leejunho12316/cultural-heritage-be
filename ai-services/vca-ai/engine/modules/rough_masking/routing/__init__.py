"""Lane-scoped ROI request routing for preprocessing-planned views."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from modules.preprocessing.contracts.views import ViewKind
from modules.rough_masking.contracts import (
    AdapterRequest,
    ImageDimensions,
    SeedRequestPaths,
    build_roi_seed_request,
)

if TYPE_CHECKING:
    from modules.preprocessing.contracts.records import ObjectAssetRecord
    from modules.preprocessing.contracts.views import ViewRecord
    from modules.shared import DetectorLane


@dataclass(frozen=True, slots=True)
class RoiRoutingInput:
    """Inputs needed to create one lane's object and tile ROI requests."""

    lane: DetectorLane
    object_view: ViewRecord
    tile_views: tuple[ViewRecord, ...]
    image_dimensions: ImageDimensions
    paths: SeedRequestPaths


def build_lane_roi_seed_requests(route: RoiRoutingInput) -> tuple[AdapterRequest, ...]:
    """Build object-crop first, then all matching ranked-tile requests."""
    object_request = build_roi_seed_request(
        route.lane,
        route.object_view,
        route.image_dimensions,
        paths=route.paths,
    )
    tile_requests = tuple(
        build_roi_seed_request(
            route.lane,
            tile_view,
            route.image_dimensions,
            paths=route.paths,
        )
        for tile_view in route.tile_views
        if _is_matching_tile(route, tile_view)
    )
    return (object_request, *tile_requests)


def seed_paths_from_preprocessing_object(
    record: ObjectAssetRecord,
    lane_output_dir: Path,
    records_json: Path,
) -> SeedRequestPaths:
    """Map preprocessing object masks into rough-mask request paths."""
    return SeedRequestPaths(
        lane_output_dir=lane_output_dir,
        records_json=records_json,
        object_mask_path=Path(record.mask.path),
    )


# 같은 lane, 같은 object_id를 갖는 RANKED_OBJECT_TILE 뷰만 통과시킨다.
# build_lane_roi_seed_requests가 타일 요청을 필터링할 때 사용한다.
def _is_matching_tile(route: RoiRoutingInput, view: ViewRecord) -> bool:
    return (
        view.kind is ViewKind.RANKED_OBJECT_TILE
        and view.lane is route.lane
        and view.object_id == route.object_view.object_id
    )
