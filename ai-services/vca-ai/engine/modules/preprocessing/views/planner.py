"""Deterministic object and scale-aware view planning without model execution."""

from hashlib import sha256

from modules.preprocessing.contracts.views import (
    BoundingBox,
    CoordinateTransform,
    LaneTilePlan,
    ObjectTarget,
    TileRankingMetadata,
    ViewKind,
    ViewManifest,
    ViewPlanningRequest,
    ViewRecord,
    ViewReuseMode,
)
from modules.preprocessing.views.tiling import (
    OVERLAP_RATIO,
    target_count,
    tile_boxes,
    tile_size_and_history,
)
from modules.shared import BUDGET_THRESHOLDS, validate_active_detector_lanes


# prefix와 부분 문자열들을 해시해 결정론적 view/object/tile id를 만든다.
# 같은 입력이면 항상 같은 id가 나와야 하므로 plan_views 전체에서
# id 생성에 이 함수만 사용한다.
def _stable_id(prefix: str, *parts: str) -> str:
    digest = sha256("|".join((prefix, *parts)).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _box_parts(bbox: BoundingBox) -> tuple[str, str, str, str]:
    return (
        f"{bbox.left:.6f}",
        f"{bbox.top:.6f}",
        f"{bbox.width:.6f}",
        f"{bbox.height:.6f}",
    )


# 타일의 네 변 중 객체 bbox 경계와 정확히 맞닿은 변의 비율을 구한다.
# _ranking에서 타일 순위 점수의 한 요소로 쓰인다.
def _boundary_overlap(tile: BoundingBox, object_bbox: BoundingBox) -> float:
    contacts = (
        tile.left == object_bbox.left,
        tile.top == object_bbox.top,
        tile.left + tile.width == object_bbox.left + object_bbox.width,
        tile.top + tile.height == object_bbox.top + object_bbox.height,
    )
    return sum(contacts) / len(contacts)


# 경계 겹침, 텍스처/색상 분산, 미커버 영역 보너스, 후보 불확실성/희소성을
# 합산해 타일 하나의 순위 점수와 근거를 만든다. plan_views에서 객체별로
# 계획된 타일마다 호출된다.
def _ranking(object_target: ObjectTarget, tile: BoundingBox) -> TileRankingMetadata:
    hints = object_target.ranking_hints
    boundary_overlap = _boundary_overlap(tile, object_target.bbox)
    uncovered_bonus = 1 - min(tile.area / object_target.bbox.area, 1.0)
    score = (
        1.0
        + boundary_overlap
        + hints.local_texture_variance
        + hints.local_color_variance
        + uncovered_bonus
        + hints.candidate_uncertainty
        + hints.candidate_scarcity
    )
    midpoint = object_target.bbox.left + object_target.bbox.width / 2
    quadrant = "left" if tile.left < midpoint else "right"
    return TileRankingMetadata(
        object_mask_coverage=1.0,
        object_boundary_overlap=boundary_overlap,
        local_texture_variance=hints.local_texture_variance,
        local_color_variance=hints.local_color_variance,
        uncovered_area_bonus=uncovered_bonus,
        candidate_uncertainty=hints.candidate_uncertainty,
        candidate_scarcity=hints.candidate_scarcity,
        spatial_diversity_reasons=("ranked_by_signals", f"spatial_anchor_{quadrant}"),
        rank_score=score,
    )


def _transform(bbox: BoundingBox) -> CoordinateTransform:
    return CoordinateTransform(
        source_bbox=bbox,
        restore_offset_x=bbox.left,
        restore_offset_y=bbox.top,
    )


def plan_views(request: ViewPlanningRequest) -> ViewManifest:
    """Plan deterministic full, object, ranked-tile, and source-view reuse records."""
    lanes = validate_active_detector_lanes(request.detector_lanes)
    full_view = ViewRecord(
        view_id=_stable_id("view", request.image_id, "full"),
        kind=ViewKind.FULL_IMAGE,
        image_id=request.image_id,
        object_id=None,
        tile_view_id=None,
        source_view_id=None,
        rag_followup_view_id=None,
        view_reuse_mode=None,
        coordinate_transform=None,
        scale_metadata=request.scale_metadata,
    )
    object_views: list[ViewRecord] = []
    tile_views: list[ViewRecord] = []
    followups: list[ViewRecord] = []
    lane_plans: list[LaneTilePlan] = []
    largest_area = max((item.bbox.area for item in request.objects), default=0.0)
    for object_target in request.objects:
        object_id = _stable_id(
            "object", request.image_id, *_box_parts(object_target.bbox)
        )
        object_view = ViewRecord(
            view_id=_stable_id("view", object_id, "crop"),
            kind=ViewKind.OBJECT_CROP,
            image_id=request.image_id,
            object_id=object_id,
            tile_view_id=None,
            source_view_id=None,
            rag_followup_view_id=None,
            view_reuse_mode=None,
            coordinate_transform=_transform(object_target.bbox),
            scale_metadata=request.scale_metadata,
        )
        object_views.append(object_view)
        followup_id = _stable_id("rag-followup", object_view.view_id)
        followups.append(
            ViewRecord(
                view_id=followup_id,
                kind=ViewKind.RAG_FOLLOWUP,
                image_id=request.image_id,
                object_id=object_id,
                tile_view_id=None,
                source_view_id=object_view.view_id,
                rag_followup_view_id=followup_id,
                view_reuse_mode=ViewReuseMode.SOURCE_VIEW,
                coordinate_transform=object_view.coordinate_transform,
                scale_metadata=request.scale_metadata,
            )
        )
        for lane in lanes:
            target = target_count(object_target.bbox.area, largest_area, lane)
            tile_size, history, strategy = tile_size_and_history(
                request, object_target, lane, target
            )
            ranked_tiles = sorted(
                (
                    (tile, _ranking(object_target, tile))
                    for tile in tile_boxes(object_target.bbox, tile_size)
                ),
                key=lambda item: (-item[1].rank_score, item[0].top, item[0].left),
            )
            lane_plans.append(
                LaneTilePlan(
                    object_id=object_id,
                    lane=lane,
                    span_halving_history=history,
                    target_tile_count=target,
                    overlap_ratio=OVERLAP_RATIO,
                    dry_run_tile_count=len(ranked_tiles),
                    tiling_strategy=strategy,
                )
            )
            for tile, ranking in ranked_tiles:
                tile_view_id = _stable_id(
                    "tile-view", request.image_id, object_id, lane, *_box_parts(tile)
                )
                tile_views.append(
                    ViewRecord(
                        view_id=tile_view_id,
                        kind=ViewKind.RANKED_OBJECT_TILE,
                        image_id=request.image_id,
                        object_id=object_id,
                        tile_view_id=tile_view_id,
                        source_view_id=object_view.view_id,
                        rag_followup_view_id=None,
                        view_reuse_mode=None,
                        coordinate_transform=_transform(tile),
                        scale_metadata=request.scale_metadata,
                        lane=lane,
                        tile_ranking=ranking,
                    )
                )
    return ViewManifest(
        image_id=request.image_id,
        full_views=(full_view,),
        object_views=tuple(object_views),
        tile_views=tuple(tile_views),
        rag_followup_views=tuple(followups),
        lane_plans=tuple(lane_plans),
        dry_run_tile_count=len(tile_views),
        requires_user_budget_approval=(
            len(tile_views) > BUDGET_THRESHOLDS.max_planned_tiles_without_approval
        ),
    )
