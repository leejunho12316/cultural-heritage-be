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
class RoiViewRequest:
    """뷰 하나 자신의 geometry, 픽셀 크기, 출력 경로.

    (전체 route가 공유하지 않고) 뷰마다 따로 묶는 이유는, 타일 crop 파일은
    각자 독립적으로 썸네일화되고(TILE_MAX_SIDE_PX 참고) object crop이나
    서로와도 픽셀 크기가 다른 경우가 매우 흔하며, 각 뷰의 detector 출력은
    반드시 자기 자신의 records.json에 남아야 하기 때문이다 - object view와
    모든 타일이 하나의 공유 records_json을 재사용하면 detector 호출이 있을
    때마다 이전 뷰의 결과를 덮어쓰게 된다.
    """

    view: ViewRecord
    image_dimensions: ImageDimensions
    paths: SeedRequestPaths


@dataclass(frozen=True, slots=True)
class RoiRoutingInput:
    """Inputs needed to create one lane's object and tile ROI requests."""

    lane: DetectorLane
    object_request: RoiViewRequest
    tile_requests: tuple[RoiViewRequest, ...]


def build_lane_roi_seed_requests(route: RoiRoutingInput) -> tuple[AdapterRequest, ...]:
    """Build object-crop first, then all matching ranked-tile requests."""
    object_request = build_roi_seed_request(
        route.lane,
        route.object_request.view,
        route.object_request.image_dimensions,
        paths=route.object_request.paths,
    )
    tile_requests = tuple(
        build_roi_seed_request(
            route.lane,
            item.view,
            item.image_dimensions,
            paths=item.paths,
        )
        for item in route.tile_requests
        if _is_matching_tile(route, item.view)
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
        and view.object_id == route.object_request.view.object_id
    )
