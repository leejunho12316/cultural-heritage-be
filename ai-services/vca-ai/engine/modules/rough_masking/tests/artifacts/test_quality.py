from __future__ import annotations

import numpy as np

from modules.rough_masking.artifacts.quality import assess_mask_quality


def test_empty_mask_is_rejected_without_crashing() -> None:
    # Given: a direct scorer call with no anomaly pixels.
    object_foreground = np.ones((20, 20), dtype=np.bool_)
    mask = np.zeros((20, 20), dtype=np.bool_)

    # When: quality is assessed without the production segmentation guard.
    decision = assess_mask_quality(mask, object_foreground)

    # Then: the scorer returns a stable rejection instead of raising.
    assert decision.accepted is False
    assert decision.reject_reason == "empty_mask"
    assert decision.quality.score == 0.0
    assert decision.quality.area_ratio == 0.0
    assert decision.quality.bbox_fill_ratio == 0.0
    assert decision.quality.boundary_pixel_ratio == 0.0
    assert decision.quality.perimeter_coverage_ratio == 0.0
    assert decision.quality.border_touch_count == 0
    assert decision.quality.component_count == 0
    assert decision.quality.largest_component_ratio == 0.0


def test_roi_frame_does_not_count_as_object_boundary() -> None:
    # Given: a full foreground object crop and a mask touching only the ROI frame.
    object_foreground = np.ones((20, 20), dtype=np.bool_)
    mask = np.zeros((20, 20), dtype=np.bool_)
    mask[0, :] = True
    mask[-1, :] = True
    mask[:, 0] = True
    mask[:, -1] = True

    # When: quality is assessed.
    decision = assess_mask_quality(mask, object_foreground)

    # Then: ROI frame pixels are edge artifacts, not object-boundary pixels.
    assert decision.quality.boundary_pixel_ratio == 0.0
    assert decision.quality.perimeter_coverage_ratio == 0.0
    assert decision.accepted is False
    assert decision.reject_reason == "edge_artifact"


def test_interior_object_outline_counts_as_object_boundary() -> None:
    # Given: an object boundary that is inside the ROI frame.
    object_foreground = np.zeros((20, 20), dtype=np.bool_)
    object_foreground[4:16, 4:16] = True
    mask = np.zeros((20, 20), dtype=np.bool_)
    mask[4, 4:16] = True
    mask[15, 4:16] = True
    mask[4:16, 4] = True
    mask[4:16, 15] = True

    # When: quality is assessed.
    decision = assess_mask_quality(mask, object_foreground)

    # Then: an interior object outline remains a boundary-only rejection.
    assert decision.quality.boundary_pixel_ratio == 1.0
    assert decision.quality.perimeter_coverage_ratio == 1.0
    assert decision.accepted is False
    assert decision.reject_reason == "boundary_only"


def test_crop_edge_localized_mask_can_remain_accepted() -> None:
    # Given: a small crop-edge anomaly that touches only one ROI border.
    object_foreground = np.ones((20, 20), dtype=np.bool_)
    mask = np.zeros((20, 20), dtype=np.bool_)
    mask[8:12, 0:3] = True

    # When: quality is assessed.
    decision = assess_mask_quality(mask, object_foreground)

    # Then: single-edge localized candidates are not rejected as object boundary.
    assert decision.quality.boundary_pixel_ratio == 0.0
    assert decision.quality.perimeter_coverage_ratio == 0.0
    assert decision.quality.border_touch_count == 1
    assert decision.accepted is True


def test_partial_boundary_mask_records_perimeter_coverage() -> None:
    # Given: a mask covering half of an interior object's boundary pixels.
    object_foreground = np.zeros((20, 20), dtype=np.bool_)
    object_foreground[4:16, 4:16] = True
    mask = np.zeros((20, 20), dtype=np.bool_)
    mask[4, 4:16] = True
    mask[5:15, 4] = True

    # When: quality is assessed.
    decision = assess_mask_quality(mask, object_foreground)

    # Then: perimeter coverage captures how much of the object outline is covered.
    assert decision.quality.boundary_pixel_ratio == 1.0
    assert decision.quality.perimeter_coverage_ratio == 0.5
