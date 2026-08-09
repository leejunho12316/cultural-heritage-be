from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from modules.anomaly_grouping.geometry import (
    load_mask_array,
    mask_area_ratio,
    mask_bbox,
    mask_containment,
    mask_iou,
    mask_polygons,
    mask_union_array,
    write_mask_png,
)
from modules.anomaly_grouping.tests.mask_test_support import rect_mask

if TYPE_CHECKING:
    from pathlib import Path

from modules.anomaly_grouping.models import BoundingBox


def test_mask_metrics_match_bbox_for_filled_rectangles(tmp_path: Path) -> None:
    # Given: two rectangle masks whose pixels exactly match their bboxes.
    left = rect_mask(tmp_path, "left", BoundingBox(0, 0, 100, 100))
    right = rect_mask(tmp_path, "right", BoundingBox(0, 0, 50, 50))

    # When: mask-pixel metrics are computed.
    iou = mask_iou(left, right)
    containment = mask_containment(right, left)
    area_ratio = mask_area_ratio(right, left)

    # Then: they match the analytic bbox values exactly (10000/2500 = 4x area).
    assert iou == 2500 / 10000
    assert containment == 1.0
    assert area_ratio == 2500 / 10000


def test_mask_union_array_combines_disjoint_regions(tmp_path: Path) -> None:
    # Given: two masks covering disjoint regions of the same canvas.
    left = rect_mask(tmp_path, "left", BoundingBox(0, 0, 10, 10))
    right = rect_mask(tmp_path, "right", BoundingBox(50, 50, 60, 60))

    # When: their pixel union is computed.
    union = mask_union_array((left, right))

    # Then: the union covers exactly the sum of both regions.
    assert int(np.count_nonzero(union)) == 100 + 100
    left_array = load_mask_array(left)
    right_array = load_mask_array(right)
    assert np.array_equal(union, left_array | right_array)


def test_mask_bbox_returns_tight_bounds_of_union(tmp_path: Path) -> None:
    # Given: a union spanning two disjoint rectangles.
    left = rect_mask(tmp_path, "left", BoundingBox(10, 10, 20, 20))
    right = rect_mask(tmp_path, "right", BoundingBox(50, 60, 70, 90))
    union = mask_union_array((left, right))

    # When: the tight bounding box is derived from the union array.
    bbox = mask_bbox(union)

    # Then: it spans from the top-left of the first rect to the bottom-right
    # of the second.
    assert (bbox.x_min, bbox.y_min, bbox.x_max, bbox.y_max) == (10.0, 10.0, 70.0, 90.0)


def test_write_mask_png_round_trips_and_is_content_addressed(tmp_path: Path) -> None:
    # Given: a small boolean mask array.
    array = np.zeros((20, 20), dtype=np.bool_)
    array[5:15, 5:15] = True

    # When: it is written to disk twice.
    first_digest = write_mask_png(array, tmp_path / "one.png")
    second_digest = write_mask_png(array, tmp_path / "two.png")

    # Then: identical pixel content produces identical digests regardless of path.
    assert first_digest == second_digest
    assert (tmp_path / "one.png").is_file()


def test_mask_polygons_outlines_a_filled_rectangle(tmp_path: Path) -> None:
    # Given: a filled rectangle mask.
    mask = rect_mask(tmp_path, "rect", BoundingBox(10, 20, 40, 60))

    # When: its outline is vectorized into polygons.
    polygons = mask_polygons(load_mask_array(mask))

    # Then: a single non-trivial closed shape is returned whose points stay
    # within the rectangle's bounds (approxPolyDP may snap corners slightly).
    assert len(polygons) == 1
    polygon = polygons[0]
    assert len(polygon) >= 3
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    assert min(xs) >= 9
    assert max(xs) <= 40
    assert min(ys) >= 19
    assert max(ys) <= 60


def test_mask_polygons_empty_mask_returns_no_polygons() -> None:
    # Given: an all-false mask array with no foreground pixels.
    array = np.zeros((20, 20), dtype=np.bool_)

    # When: it is vectorized.
    polygons = mask_polygons(array)

    # Then: no contour exists to outline.
    assert polygons == ()


def test_mask_polygons_returns_one_polygon_per_disconnected_fragment(
    tmp_path: Path,
) -> None:
    # Given: a mask made of two disjoint rectangles - real SAM2 masks are
    # often multi-component (e.g. scattered corrosion spots), so a single
    # "largest contour" polygon would silently drop the smaller fragment.
    left = rect_mask(tmp_path, "left", BoundingBox(0, 0, 10, 10))
    right = rect_mask(tmp_path, "right", BoundingBox(50, 50, 70, 70))
    union = mask_union_array((left, right))

    # When: the union mask is vectorized.
    polygons = mask_polygons(union)

    # Then: both fragments are represented, largest area first.
    assert len(polygons) == 2
    areas = [
        (max(p[0] for p in polygon) - min(p[0] for p in polygon))
        * (max(p[1] for p in polygon) - min(p[1] for p in polygon))
        for polygon in polygons
    ]
    assert areas[0] > areas[1]


def test_mask_polygons_keeps_tiny_fragments_instead_of_collapsing_to_a_point() -> None:
    # Given: a mask with one large blob and one 2x2-pixel speck - the speck's
    # raw contour is too small for approxPolyDP's perimeter-based epsilon to
    # simplify without collapsing to a degenerate 1-2 point shape.
    array = np.zeros((100, 100), dtype=np.bool_)
    array[10:40, 10:40] = True
    array[90:92, 90:92] = True

    # When: it is vectorized.
    polygons = mask_polygons(array)

    # Then: both fragments survive as valid (>=3 point) closed shapes rather
    # than the speck being silently dropped or reduced to 1-2 points.
    assert len(polygons) == 2
    assert all(len(polygon) >= 3 for polygon in polygons)
