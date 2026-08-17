from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest
from PIL import Image

from modules.shared.errors import ContractValidationError
from modules.shared.mask_pixels import (
    array_bbox,
    load_mask_array,
    mask_union_array,
    write_mask_png,
)

if TYPE_CHECKING:
    from pathlib import Path

_CANVAS_SIZE = 200


def _rect_mask(tmp_path: Path, name: str, box: tuple[int, int, int, int]) -> Path:
    array = np.zeros((_CANVAS_SIZE, _CANVAS_SIZE), dtype=np.uint8)
    x_min, y_min, x_max, y_max = box
    array[y_min:y_max, x_min:x_max] = 255
    path = tmp_path / f"{name}.png"
    Image.fromarray(array).save(path)
    return path


def test_load_mask_array_reads_filled_rectangle_as_foreground(tmp_path: Path) -> None:
    # Given: a filled-rectangle mask PNG.
    path = _rect_mask(tmp_path, "rect", (10, 10, 20, 20))

    # When: it is loaded as a boolean array.
    array = load_mask_array(path)

    # Then: exactly the rectangle's pixels are foreground.
    assert int(np.count_nonzero(array)) == 100


def test_mask_union_array_combines_disjoint_regions(tmp_path: Path) -> None:
    # Given: two masks covering disjoint regions of the same canvas.
    left = _rect_mask(tmp_path, "left", (0, 0, 10, 10))
    right = _rect_mask(tmp_path, "right", (50, 50, 60, 60))

    # When: their pixel union is computed.
    union = mask_union_array((left, right))

    # Then: the union covers exactly the sum of both regions.
    assert int(np.count_nonzero(union)) == 100 + 100
    assert np.array_equal(union, load_mask_array(left) | load_mask_array(right))


def test_array_bbox_returns_tight_bounds_of_union(tmp_path: Path) -> None:
    # Given: a union spanning two disjoint rectangles.
    left = _rect_mask(tmp_path, "left", (10, 10, 20, 20))
    right = _rect_mask(tmp_path, "right", (50, 60, 70, 90))
    union = mask_union_array((left, right))

    # When: the tight bounding box is derived from the union array.
    bbox = array_bbox(union)

    # Then: it spans from the top-left of the first rect to the bottom-right
    # of the second.
    assert bbox == (10.0, 10.0, 70.0, 90.0)


def test_array_bbox_rejects_an_empty_mask() -> None:
    # Given: an all-false mask array with no foreground pixels.
    array = np.zeros((20, 20), dtype=np.bool_)

    # When/Then: it fails loudly with a typed contract error, not a crash.
    with pytest.raises(ContractValidationError):
        array_bbox(array)


def test_write_mask_png_round_trips_and_is_content_addressed(tmp_path: Path) -> None:
    # Given: a small boolean mask array.
    array = np.zeros((20, 20), dtype=np.bool_)
    array[5:15, 5:15] = True

    # When: it is written to disk twice under different paths.
    first_digest = write_mask_png(array, tmp_path / "one.png")
    second_digest = write_mask_png(array, tmp_path / "two.png")

    # Then: identical pixel content produces identical digests regardless of path.
    assert first_digest == second_digest
    assert (tmp_path / "one.png").is_file()
