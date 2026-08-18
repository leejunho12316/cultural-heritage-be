"""Pixel-grounded D010 ranking hints for one detected object.

The spec (T4/D010) names four signals - local_texture_variance,
local_color_variance, candidate_uncertainty, candidate_scarcity - but never
prescribes an exact formula, only that each is normalized to [0, 1]. No
producer existed anywhere in the codebase before this module; every caller
was passing hardcoded zeros. These formulas are this project's first real
implementation, not a transcription of a pre-existing spec formula:

- local_texture_variance: variance of the grayscale gradient magnitude
  across the object crop. Squashed with v / (v + k) rather than clipped, so
  a uniform crop reads near 0 and a highly textured one approaches 1
  without a hard cutoff.
- local_color_variance: mean per-channel RGB variance across the crop,
  squashed the same way. A single flat color reads near 0.
- candidate_uncertainty: 1 - the upstream object-detector confidence
  (already a real, available 0-1 score) - a less-confident object
  localization warrants more thorough tile coverage to compensate.
- candidate_scarcity: how much of the object's own bounding box its mask
  actually fills (1 - foreground_ratio). A sparse/irregular mask relative
  to its bbox suggests the true target region is harder to pin down, so
  scarcity is higher.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from modules.preprocessing.contracts.views import ObjectRankingHints

if TYPE_CHECKING:
    from pathlib import Path

    from numpy.typing import NDArray

_TEXTURE_SQUASH_K = 400.0
_COLOR_SQUASH_K = 3000.0
_MIN_GRADIENT_SIDE_PX = 2


def _squash(value: float, k: float) -> float:
    return float(value / (value + k)) if value > 0 else 0.0


def _texture_variance(rgb: NDArray[np.uint8]) -> float:
    # np.gradient는 각 축에 최소 2개 원소를 요구한다 - 아주 작은(1px 폭/높이)
    # crop에 대해서도 예외 없이 값을 내야 하므로, 그런 경우는 "텍스처 신호
    # 없음"으로 보고 0을 돌려준다(다른 퇴화 케이스인 _mask_fill_ratio의
    # size==0 처리와 같은 방침).
    if rgb.shape[0] < _MIN_GRADIENT_SIDE_PX or rgb.shape[1] < _MIN_GRADIENT_SIDE_PX:
        return 0.0
    grayscale = rgb.mean(axis=2)
    gradient_y, gradient_x = np.gradient(grayscale)
    magnitude = np.hypot(gradient_x, gradient_y)
    return _squash(float(magnitude.var()), _TEXTURE_SQUASH_K)


def _color_variance(rgb: NDArray[np.uint8]) -> float:
    channel_variances = rgb.astype(np.float64).var(axis=(0, 1))
    return _squash(float(channel_variances.mean()), _COLOR_SQUASH_K)


def _mask_fill_ratio(mask: NDArray[np.bool_]) -> float:
    if mask.size == 0:
        return 0.0
    return float(mask.sum()) / float(mask.size)


def compute_object_ranking_hints(
    bbox_crop_path: Path,
    mask_path: Path,
    detection_score: float,
) -> ObjectRankingHints:
    """Measure real texture/color/uncertainty/scarcity hints for one object."""
    from PIL import Image  # noqa: PLC0415

    with Image.open(bbox_crop_path) as crop_image:
        rgb = np.asarray(crop_image.convert("RGB"))
    with Image.open(mask_path) as mask_image:
        mask = np.asarray(mask_image.convert("L")) > 0

    clamped_score = min(max(detection_score, 0.0), 1.0)
    return ObjectRankingHints(
        local_texture_variance=_texture_variance(rgb),
        local_color_variance=_color_variance(rgb),
        candidate_uncertainty=1.0 - clamped_score,
        candidate_scarcity=1.0 - _mask_fill_ratio(mask),
    )
