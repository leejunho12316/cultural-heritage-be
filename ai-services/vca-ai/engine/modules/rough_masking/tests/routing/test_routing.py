from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from modules.preprocessing import BoundingBox, CoordinateTransform, ViewKind
from modules.preprocessing.contracts.records import (
    MaterializedAssetRecord,
    ObjectAssetRecord,
)
from modules.rough_masking import ImageDimensions, SeedRequestPaths
from modules.rough_masking.routing import (
    RoiRoutingInput,
    build_lane_roi_seed_requests,
    seed_paths_from_preprocessing_object,
)
from modules.rough_masking.tests.artifacts.test_t4_generation import (
    make_object_mask_path,
    make_roi_view,
)
from modules.rough_masking.tests.candidates.test_t4 import make_view
from modules.shared import DetectorLane

if TYPE_CHECKING:
    from pathlib import Path

    from modules.preprocessing import ScaleMetadata, ViewRecord


def _tile_view(object_view: ViewRecord, tile_id: str, lane: DetectorLane) -> ViewRecord:
    return replace(
        object_view,
        view_id=tile_id,
        kind=ViewKind.RANKED_OBJECT_TILE,
        tile_view_id=tile_id,
        source_view_id=object_view.view_id,
        lane=lane,
        coordinate_transform=CoordinateTransform(
            source_bbox=BoundingBox(12.0, 14.0, 20.0, 16.0),
            restore_offset_x=12.0,
            restore_offset_y=14.0,
        ),
    )


def _routing_input(
    tmp_path: Path, lane: DetectorLane, tile_views: tuple[ViewRecord, ...]
) -> RoiRoutingInput:
    return RoiRoutingInput(
        lane=lane,
        object_view=make_roi_view(),
        tile_views=tile_views,
        image_dimensions=ImageDimensions(64, 48),
        paths=SeedRequestPaths(
            lane_output_dir=tmp_path / lane.value,
            records_json=tmp_path / lane.value / "records.json",
            object_mask_path=make_object_mask_path(tmp_path),
        ),
    )


def _asset(path: Path) -> MaterializedAssetRecord:
    return MaterializedAssetRecord(
        path=str(path),
        sha256="0" * 64,
        media_type="image/png",
    )


def _preprocessing_object(mask_path: Path, scale: ScaleMetadata) -> ObjectAssetRecord:
    asset = _asset(mask_path)
    return ObjectAssetRecord(
        candidate_id="candidate-001",
        object_id="object-001",
        image_id="image-001",
        lane=DetectorLane.OWLV2_SAM2,
        accepted=True,
        diagnostics=(),
        bbox_xyxy=(0.0, 0.0, 64.0, 48.0),
        score=0.7,
        sam2_score=0.9,
        prompt_pack_id="static-seed-minimal-pack-v1",
        prompt_role="static_seed",
        prompt_text="surface crack",
        generated_prompt_id="prompt-001",
        source_terms=(),
        detector_model_id="detector",
        detector_model_revision="revision",
        sam2_model_id="sam2",
        sam2_model_revision="revision",
        device="mps",
        source_image_sha256="1" * 64,
        detector_input_sha256="2" * 64,
        scale_metadata=scale,
        scale_removal_applied=False,
        mask=asset,
        bbox_crop=asset,
        alpha_cutout=asset,
        detection_overlay=asset,
        tile=asset,
        tiles=(asset,),
    )


def test_lane_routing_emits_object_crop_then_only_matching_ranked_tiles(
    tmp_path: Path,
) -> None:
    # Given: one object crop and ranked tiles from multiple detector lanes.
    object_view = make_roi_view()
    owl_tile = _tile_view(object_view, "tile-owl", DetectorLane.OWLV2_SAM2)
    florence_tile = _tile_view(
        object_view,
        "tile-florence",
        DetectorLane.FLORENCE2_SAM2,
    )
    routing_input = _routing_input(
        tmp_path,
        DetectorLane.OWLV2_SAM2,
        (florence_tile, owl_tile),
    )

    # When: rough_masking builds lane-scoped ROI seed requests.
    requests = build_lane_roi_seed_requests(routing_input)

    # Then: the object crop is first and only lane-matched tiles follow.
    assert [request.view.kind for request in requests] == [
        ViewKind.OBJECT_CROP,
        ViewKind.RANKED_OBJECT_TILE,
    ]
    assert requests[0].view.tile_view_id is None
    assert requests[1].view.tile_view_id == "tile-owl"
    assert all(request.lane is DetectorLane.OWLV2_SAM2 for request in requests)


def test_lane_routing_preserves_every_matching_tile_without_truncation(
    tmp_path: Path,
) -> None:
    # Given: three matching ranked tiles and one unrelated full-image view.
    object_view = make_roi_view()
    tile_views = tuple(
        _tile_view(object_view, f"tile-{index}", DetectorLane.GROUNDED_SAM2)
        for index in range(3)
    )
    routing_input = _routing_input(
        tmp_path,
        DetectorLane.GROUNDED_SAM2,
        (*tile_views, make_view()),
    )

    # When: rough_masking builds requests from preprocessing-planned views.
    requests = build_lane_roi_seed_requests(routing_input)

    # Then: every matching tile is retained after the object-crop seed request.
    assert [request.view.tile_view_id for request in requests] == [
        None,
        "tile-0",
        "tile-1",
        "tile-2",
    ]


def test_preprocessing_object_paths_feed_object_mask_to_roi_request(
    tmp_path: Path,
) -> None:
    # Given: a preprocessing object record with its foreground mask.
    mask_path = make_object_mask_path(tmp_path)
    record = _preprocessing_object(
        mask_path,
        make_roi_view().scale_metadata,
    )

    # When: rough_masking converts the handoff object into request paths.
    paths = seed_paths_from_preprocessing_object(
        record,
        tmp_path / "lane",
        tmp_path / "lane" / "records.json",
    )

    # Then: rough_masking receives only the preprocessing object foreground mask.
    assert paths.object_mask_path == mask_path
