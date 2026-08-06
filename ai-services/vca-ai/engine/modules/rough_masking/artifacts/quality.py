"""Mask quality scoring for clipped rough-mask candidates."""

# ruff: noqa: PLC0415

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray


QUALITY_FILTER_VERSION: Final = "rough-mask-quality-v1"
_BROAD_BLOB_MIN_AREA_RATIO: Final = 0.12
_BROAD_BLOB_MIN_FILL_RATIO: Final = 0.55
_BOUNDARY_ONLY_MIN_AREA_RATIO: Final = 0.02
_BOUNDARY_ONLY_MIN_BOUNDARY_RATIO: Final = 0.70
_EDGE_ARTIFACT_MIN_AREA_RATIO: Final = 0.10
_EDGE_ARTIFACT_MIN_BOUNDARY_RATIO: Final = 0.35
_MULTI_BORDER_TOUCH_COUNT: Final = 2


@dataclass(frozen=True, slots=True)
class _MaskQuality:
    filter_version: str
    score: float
    area_ratio: float
    bbox_fill_ratio: float
    boundary_pixel_ratio: float
    perimeter_coverage_ratio: float
    border_touch_count: int
    component_count: int
    largest_component_ratio: float


@dataclass(frozen=True, slots=True)
class _QualityDecision:
    quality: _MaskQuality
    accepted: bool
    reject_reason: str | None


@dataclass(frozen=True, slots=True)
class _MaskBounds:
    left: int
    top: int
    right: int
    bottom: int


def assess_mask_quality(
    mask: NDArray[np.bool_], object_foreground: NDArray[np.bool_]
) -> _QualityDecision:
    """Score one clipped mask and decide whether v1 should keep it."""
    import numpy as np

    area_px = int(np.count_nonzero(mask))
    if area_px == 0:
        quality = _MaskQuality(
            QUALITY_FILTER_VERSION,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0,
            0,
            0.0,
        )
        return _QualityDecision(quality, accepted=False, reject_reason="empty_mask")
    area_ratio = float(area_px) / float(mask.size)
    bounds = _mask_bounds(mask)
    bbox_fill_ratio = _bbox_fill_ratio(area_px, bounds)
    object_boundary = _object_boundary(object_foreground)
    boundary_pixel_ratio = _boundary_pixel_ratio(mask, object_boundary, area_px)
    perimeter_coverage_ratio = _perimeter_coverage_ratio(mask, object_boundary)
    border_touch_count = _border_touch_count(mask)
    component_count, largest_component_ratio = _component_stats(mask, area_px)
    quality = _MaskQuality(
        QUALITY_FILTER_VERSION,
        _quality_score(area_ratio, boundary_pixel_ratio, largest_component_ratio),
        area_ratio,
        bbox_fill_ratio,
        boundary_pixel_ratio,
        perimeter_coverage_ratio,
        border_touch_count,
        component_count,
        largest_component_ratio,
    )
    reject_reason = _reject_reason(quality, area_px)
    return _QualityDecision(quality, reject_reason is None, reject_reason)


def _mask_bounds(mask: NDArray[np.bool_]) -> _MaskBounds:
    import numpy as np

    rows, columns = np.nonzero(mask)
    return _MaskBounds(
        int(columns.min()),
        int(rows.min()),
        int(columns.max()) + 1,
        int(rows.max()) + 1,
    )


def _bbox_fill_ratio(area_px: int, bounds: _MaskBounds) -> float:
    bbox_area = (bounds.right - bounds.left) * (bounds.bottom - bounds.top)
    return float(area_px) / float(bbox_area)


def _object_boundary(object_foreground: NDArray[np.bool_]) -> NDArray[np.bool_]:
    boundary = object_foreground & ~_erode_3x3(object_foreground)
    return _without_roi_frame(boundary)


def _boundary_pixel_ratio(
    mask: NDArray[np.bool_], object_boundary: NDArray[np.bool_], area_px: int
) -> float:
    import numpy as np

    return float(np.count_nonzero(mask & object_boundary)) / float(area_px)


def _perimeter_coverage_ratio(
    mask: NDArray[np.bool_], object_boundary: NDArray[np.bool_]
) -> float:
    import numpy as np

    boundary_px = int(np.count_nonzero(object_boundary))
    if boundary_px == 0:
        return 0.0
    return float(np.count_nonzero(mask & object_boundary)) / float(boundary_px)


def _without_roi_frame(mask: NDArray[np.bool_]) -> NDArray[np.bool_]:
    framed = mask.copy()
    framed[0, :] = False
    framed[-1, :] = False
    framed[:, 0] = False
    framed[:, -1] = False
    return framed


def _erode_3x3(mask: NDArray[np.bool_]) -> NDArray[np.bool_]:
    import numpy as np

    padded = np.pad(mask, 1, mode="constant", constant_values=False)
    eroded = np.ones(mask.shape, dtype=np.bool_)
    for row_offset in range(3):
        for column_offset in range(3):
            eroded &= padded[
                row_offset : row_offset + mask.shape[0],
                column_offset : column_offset + mask.shape[1],
            ]
    return eroded


def _border_touch_count(mask: NDArray[np.bool_]) -> int:
    return sum(
        (
            bool(mask[0, :].any()),
            bool(mask[-1, :].any()),
            bool(mask[:, 0].any()),
            bool(mask[:, -1].any()),
        )
    )


def _component_stats(mask: NDArray[np.bool_], area_px: int) -> tuple[int, float]:
    import numpy as np

    visited = np.zeros(mask.shape, dtype=np.bool_)
    component_count = 0
    largest_component_px = 0
    rows, columns = np.nonzero(mask)
    for row, column in zip(rows.tolist(), columns.tolist(), strict=True):
        if visited[row, column]:
            continue
        component_count += 1
        component_px = _flood_component(mask, visited, row, column)
        largest_component_px = max(largest_component_px, component_px)
    return component_count, float(largest_component_px) / float(area_px)


def _flood_component(
    mask: NDArray[np.bool_],
    visited: NDArray[np.bool_],
    start_row: int,
    start_column: int,
) -> int:
    stack = [(start_row, start_column)]
    visited[start_row, start_column] = True
    component_px = 0
    while stack:
        row, column = stack.pop()
        component_px += 1
        for next_row, next_column in _neighbors(row, column, mask.shape):
            if visited[next_row, next_column] or not mask[next_row, next_column]:
                continue
            visited[next_row, next_column] = True
            stack.append((next_row, next_column))
    return component_px


def _neighbors(
    row: int, column: int, shape: tuple[int, int]
) -> tuple[tuple[int, int], ...]:
    height, width = shape
    neighbors: list[tuple[int, int]] = []
    if row > 0:
        neighbors.append((row - 1, column))
    if row + 1 < height:
        neighbors.append((row + 1, column))
    if column > 0:
        neighbors.append((row, column - 1))
    if column + 1 < width:
        neighbors.append((row, column + 1))
    return tuple(neighbors)


def _quality_score(
    area_ratio: float, boundary_pixel_ratio: float, largest_component_ratio: float
) -> float:
    area_penalty = min(area_ratio / _BROAD_BLOB_MIN_AREA_RATIO, 1.0) * 0.25
    boundary_penalty = min(boundary_pixel_ratio, 1.0) * 0.35
    fragmentation_penalty = (1.0 - largest_component_ratio) * 0.20
    return max(0.0, 1.0 - area_penalty - boundary_penalty - fragmentation_penalty)


def _reject_reason(quality: _MaskQuality, area_px: int) -> str | None:
    _ = area_px
    if (
        quality.area_ratio >= _BROAD_BLOB_MIN_AREA_RATIO
        and quality.bbox_fill_ratio >= _BROAD_BLOB_MIN_FILL_RATIO
    ):
        return "broad_texture_blob"
    if (
        quality.area_ratio >= _BOUNDARY_ONLY_MIN_AREA_RATIO
        and quality.boundary_pixel_ratio >= _BOUNDARY_ONLY_MIN_BOUNDARY_RATIO
    ):
        return "boundary_only"
    if (
        quality.border_touch_count >= _MULTI_BORDER_TOUCH_COUNT
        and quality.area_ratio >= _EDGE_ARTIFACT_MIN_AREA_RATIO
    ):
        return "edge_artifact"
    return None
