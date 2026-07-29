from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


@dataclass
class Fragment:
    index: int
    path: Path
    name: str
    image: np.ndarray
    gray: np.ndarray
    mask: np.ndarray
    crop_bbox_xywh: tuple[int, int, int, int]
    mask_area: int
    principal_angle_deg: float
    diagnostics: dict[str, Any] = field(default_factory=dict)
    source_path: Path | None = None
    source_name: str | None = None
    source_index: int | None = None
    subfragment_index: int = 0


@dataclass
class Placement:
    fragment_index: int
    center_x: float
    center_y: float
    rotation_deg: float


@dataclass
class SearchGeometry:
    target_mask_full: np.ndarray
    target_preview_full: np.ndarray
    target_mask_opt: np.ndarray
    search_scale_x: float
    search_scale_y: float
    canvas_width_full: int
    canvas_height_full: int
    reference_scale: float
    target_offset_xy: tuple[int, int]


@dataclass
class ScoreBreakdown:
    total: float
    iou: float
    outside_ratio: float
    overlap_ratio: float
    missing_ratio: float
    boundary_f1: float

    def to_dict(self) -> dict[str, float]:
        return {
            "total": float(self.total),
            "iou": float(self.iou),
            "outside_ratio": float(self.outside_ratio),
            "overlap_ratio": float(self.overlap_ratio),
            "missing_ratio": float(self.missing_ratio),
            "boundary_f1": float(self.boundary_f1),
        }
