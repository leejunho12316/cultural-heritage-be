"""Geometry metrics used by relation authority."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from modules.anomaly_grouping.models import BoundingBox


def intersection_area(left: BoundingBox, right: BoundingBox) -> float:
    """Return the overlapping bbox area in source units."""
    width = max(0.0, min(left.x_max, right.x_max) - max(left.x_min, right.x_min))
    height = max(0.0, min(left.y_max, right.y_max) - max(left.y_min, right.y_min))
    return width * height


def iou(left: BoundingBox, right: BoundingBox) -> float:
    """Return intersection-over-union for two positive-area boxes."""
    intersection = intersection_area(left, right)
    union = left.area + right.area - intersection
    if union <= 0.0:
        return 0.0
    return intersection / union


def containment(child: BoundingBox, parent: BoundingBox) -> float:
    """Return the fraction of child area covered by parent."""
    return intersection_area(child, parent) / child.area


def area_ratio(child: BoundingBox, parent: BoundingBox) -> float:
    """Return child area divided by parent area."""
    return child.area / parent.area


def overlaps(left: BoundingBox, right: BoundingBox) -> bool:
    """Return whether two boxes have positive intersection area."""
    return intersection_area(left, right) > 0.0
