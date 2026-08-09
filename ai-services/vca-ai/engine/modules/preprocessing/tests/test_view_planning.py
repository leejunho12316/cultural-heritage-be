from __future__ import annotations

import pytest

from modules import preprocessing
from modules.shared import BUDGET_THRESHOLDS


def _scale(confidence: preprocessing.ScaleConfidence) -> preprocessing.ScaleMetadata:
    match confidence:
        case preprocessing.ScaleConfidence.HIGH:
            return preprocessing.ScaleMetadata(
                scale_marker_detected=True,
                scale_marker_bbox=preprocessing.BoundingBox(10, 10, 40, 8),
                scale_marker_width_px=40,
                scale_unit_px=100,
                scale_unit_source="detected_scale_marker",
                scale_confidence=confidence,
                confidence_reasons=("marker geometry is consistent",),
                fallback_reason=None,
            )
        case preprocessing.ScaleConfidence.LOW:
            return preprocessing.ScaleMetadata(
                scale_marker_detected=True,
                scale_marker_bbox=preprocessing.BoundingBox(10, 10, 40, 8),
                scale_marker_width_px=40,
                scale_unit_px=100,
                scale_unit_source="weak_scale_marker",
                scale_confidence=confidence,
                confidence_reasons=("marker is partially occluded",),
                fallback_reason="scale_confidence_low",
            )
        case preprocessing.ScaleConfidence.MEDIUM:
            return preprocessing.ScaleMetadata(
                scale_marker_detected=True,
                scale_marker_bbox=preprocessing.BoundingBox(10, 10, 40, 8),
                scale_marker_width_px=40,
                scale_unit_px=100,
                scale_unit_source="detected_scale_marker",
                scale_confidence=confidence,
                confidence_reasons=("marker geometry is partially consistent",),
                fallback_reason=None,
            )
        case preprocessing.ScaleConfidence.UNAVAILABLE:
            return preprocessing.ScaleMetadata(
                scale_marker_detected=False,
                scale_marker_bbox=None,
                scale_marker_width_px=None,
                scale_unit_px=None,
                scale_unit_source=None,
                scale_confidence=confidence,
                confidence_reasons=("no scale marker detected",),
                fallback_reason="scale_marker_unavailable",
            )


def _request(
    scale: preprocessing.ScaleMetadata,
    objects: tuple[preprocessing.ObjectTarget, ...],
) -> preprocessing.ViewPlanningRequest:
    return preprocessing.ViewPlanningRequest(
        image_id=preprocessing.ImageId("image-fixture"),
        image_width_px=500,
        image_height_px=500,
        objects=objects,
        scale_metadata=scale,
    )


def _object(
    left: float, top: float, width: float, height: float
) -> preprocessing.ObjectTarget:
    return preprocessing.ObjectTarget(
        bbox=preprocessing.BoundingBox(left, top, width, height),
        ranking_hints=preprocessing.ObjectRankingHints(
            local_texture_variance=0.8,
            local_color_variance=0.6,
            candidate_uncertainty=0.4,
            candidate_scarcity=0.5,
        ),
    )


def test_high_scale_plan_has_full_object_ranked_tiles_and_source_view_reuse() -> None:
    # Given: a high-confidence scale marker and one sizable object target.
    request = _request(
        _scale(preprocessing.ScaleConfidence.HIGH),
        (_object(0, 0, 500, 500),),
    )

    # When: a dry-run view manifest is planned without model execution.
    manifest = preprocessing.plan_views(request)

    # Then: every declared view role has stable IDs and explicit restore metadata.
    assert len(manifest.full_views) == 1
    assert manifest.full_views[0].kind is preprocessing.ViewKind.FULL_IMAGE
    assert manifest.full_views[0].coordinate_transform is None
    assert manifest.object_views[0].coordinate_transform is not None
    assert manifest.tile_views
    assert (
        manifest.rag_followup_views[0].view_reuse_mode
        is preprocessing.ViewReuseMode.SOURCE_VIEW
    )
    assert (
        manifest.rag_followup_views[0].source_view_id
        == manifest.object_views[0].view_id
    )
    assert all(
        tile.object_id == manifest.object_views[0].object_id
        for tile in manifest.tile_views
    )
    assert all(tile.coordinate_transform is not None for tile in manifest.tile_views)
    assert all(tile.tile_ranking is not None for tile in manifest.tile_views)
    assert manifest.lane_plans[0].span_halving_history == (2.0, 1.0, 0.5)
    assert manifest.dry_run_tile_count == len(manifest.tile_views)


def test_low_and_unavailable_scale_use_largest_object_fallback() -> None:
    # Given: equivalent object geometry with low and unavailable scale confidence.
    objects = (_object(20, 20, 300, 300),)

    # When: both manifests are planned.
    low = preprocessing.plan_views(
        _request(_scale(preprocessing.ScaleConfidence.LOW), objects)
    )
    unavailable = preprocessing.plan_views(
        _request(_scale(preprocessing.ScaleConfidence.UNAVAILABLE), objects)
    )

    # Then: neither assumes scale spans and both retain every D021 metadata field.
    for manifest in (low, unavailable):
        assert all(
            plan.tiling_strategy == "largest_object_fallback"
            for plan in manifest.lane_plans
        )
        assert all(plan.span_halving_history == () for plan in manifest.lane_plans)
        scale = manifest.tile_views[0].scale_metadata
        assert scale.fallback_reason is not None
        assert scale.confidence_reasons


def test_object_size_adjusts_lane_targets_and_rankings_are_complete() -> None:
    # Given: a largest object and a quarter-area object with equal input signals.
    objects = (_object(0, 0, 400, 400), _object(50, 50, 100, 100))

    # When: scale-aware tiling plans all active detector lanes without global caps.
    manifest = preprocessing.plan_views(
        _request(_scale(preprocessing.ScaleConfidence.HIGH), objects)
    )

    # Then: target counts use the specified square-root formula and rank signals exist.
    targets = {
        (plan.object_id, plan.lane): plan.target_tile_count
        for plan in manifest.lane_plans
    }
    largest_id = manifest.object_views[0].object_id
    smaller_id = manifest.object_views[1].object_id
    assert largest_id is not None
    assert smaller_id is not None
    assert targets[(largest_id, preprocessing.DetectorLane.OWLV2_SAM2)] == 64
    assert targets[(smaller_id, preprocessing.DetectorLane.OWLV2_SAM2)] == 16
    assert targets[(largest_id, preprocessing.DetectorLane.GROUNDED_SAM2)] == 36
    assert targets[(smaller_id, preprocessing.DetectorLane.GROUNDED_SAM2)] == 9
    assert targets[(largest_id, preprocessing.DetectorLane.FLORENCE2_SAM2)] == 16
    assert targets[(smaller_id, preprocessing.DetectorLane.FLORENCE2_SAM2)] == 4
    ranking = manifest.tile_views[0].tile_ranking
    assert ranking is not None
    assert ranking.object_mask_coverage >= preprocessing.MIN_OBJECT_TILE_OVERLAP
    assert ranking.object_boundary_overlap >= 0
    assert ranking.local_texture_variance == 0.8
    assert ranking.local_color_variance == 0.6
    assert ranking.uncovered_area_bonus >= 0
    assert ranking.candidate_uncertainty == 0.4
    assert ranking.candidate_scarcity == 0.5
    assert ranking.spatial_diversity_reasons
    assert manifest.requires_user_budget_approval is False


def test_large_dry_run_requires_budget_approval_without_tile_truncation() -> None:
    # Given: three large object targets whose planned tiles exceed the shared limit.
    objects = tuple(_object(float(index), 0, 500, 500) for index in range(3))

    # When: the full dry-run view plan is calculated.
    manifest = preprocessing.plan_views(
        _request(_scale(preprocessing.ScaleConfidence.HIGH), objects)
    )

    # Then: approval is required while every tile remains represented in the manifest.
    assert (
        manifest.dry_run_tile_count
        > BUDGET_THRESHOLDS.max_planned_tiles_without_approval
    )
    assert manifest.requires_user_budget_approval is True
    assert manifest.dry_run_tile_count == sum(
        plan.dry_run_tile_count for plan in manifest.lane_plans
    )


def test_invalid_scale_zero_area_and_insufficient_overlap_fail_closed() -> None:
    # Given: malformed scale, object geometry, and tile ranking inputs.
    def invalid_marker() -> preprocessing.ScaleMetadata:
        return preprocessing.ScaleMetadata(
            scale_marker_detected=True,
            scale_marker_bbox=preprocessing.BoundingBox(0, 0, 5, 5),
            scale_marker_width_px=0,
            scale_unit_px=5,
            scale_unit_source="invalid",
            scale_confidence=preprocessing.ScaleConfidence.HIGH,
            confidence_reasons=("bad marker",),
            fallback_reason=None,
        )

    def zero_area_object() -> preprocessing.ObjectTarget:
        return _object(0, 0, 0, 10)

    def insufficient_overlap() -> preprocessing.TileRankingMetadata:
        return preprocessing.TileRankingMetadata(
            object_mask_coverage=0.01,
            object_boundary_overlap=0.2,
            local_texture_variance=0.1,
            local_color_variance=0.1,
            uncovered_area_bonus=0.1,
            candidate_uncertainty=0.1,
            candidate_scarcity=0.1,
            spatial_diversity_reasons=("fixture",),
            rank_score=0.1,
        )

    # When: each malformed typed boundary is constructed.
    # Then: invalid data is rejected before it can become a view record.
    with pytest.raises(preprocessing.ContractValidationError):
        _ = invalid_marker()
    with pytest.raises(preprocessing.ContractValidationError):
        _ = zero_area_object()
    with pytest.raises(preprocessing.ContractValidationError):
        _ = insufficient_overlap()
