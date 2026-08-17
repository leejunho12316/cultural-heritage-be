"""Shared mask fixture helpers for anomaly_grouping tests.

A filled rectangle mask is pixel-identical to its own bounding box, so tests
that used to reason purely in bbox terms keep the same IoU/containment
numbers once the rectangle is rasterized - only the classification rule
(now mask-first) actually changed.
"""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

from modules.anomaly_grouping.models import MaskReference

if TYPE_CHECKING:
    from pathlib import Path

    from modules.anomaly_grouping.models import BoundingBox

_CANVAS_SIZE = 200


def rect_mask(tmp_path: Path, name: str, bbox: BoundingBox) -> MaskReference:
    """Write a filled-rectangle mask PNG matching bbox exactly."""
    array = np.zeros((_CANVAS_SIZE, _CANVAS_SIZE), dtype=np.uint8)
    x_min, y_min = round(bbox.x_min), round(bbox.y_min)
    x_max, y_max = round(bbox.x_max), round(bbox.y_max)
    array[y_min:y_max, x_min:x_max] = 255
    path = tmp_path / f"{name}.png"
    Image.fromarray(array).save(path)
    digest = sha256(path.read_bytes()).hexdigest()
    return MaskReference(str(path), digest)
