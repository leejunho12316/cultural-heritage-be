"""탐지된 객체 하나에 대한, 픽셀 기반 D010 랭킹 힌트.

스펙(T4/D010)은 local_texture_variance, local_color_variance,
candidate_uncertainty, candidate_scarcity 네 가지 신호를 지정하지만,
정확한 공식은 규정하지 않고 각각 [0, 1]로 정규화한다는 것만 정한다. 이
모듈이 생기기 전까지는 코드베이스 어디에도 이걸 만드는 producer가 없었다;
모든 호출자는 하드코딩된 0을 넘기고 있었다. 아래 공식들은 기존 스펙
공식을 옮겨온 게 아니라 이 프로젝트의 첫 실제 구현이다:

- local_texture_variance: object crop 전체에서 그레이스케일 gradient
  크기의 분산. clip 대신 v / (v + k)로 압축해서, 균일한 crop은 0에
  가깝게, 텍스처가 많은 crop은 하드 컷오프 없이 1에 가깝게 나온다.
- local_color_variance: crop 전체의 채널별 RGB 분산 평균, 같은 방식으로
  압축. 단색에 가까운 crop은 0에 가깝게 나온다.
- candidate_uncertainty: 1 - 업스트림 object-detector의 신뢰도(이미 실제로
  사용 가능한 0-1 점수) - 객체 위치 추정 신뢰도가 낮을수록 이를 보완하기
  위해 더 촘촘한 타일 커버리지가 필요하다.
- candidate_scarcity: 객체 자신의 bounding box 중 마스크가 실제로 채우고
  있는 비율(1 - foreground_ratio). bbox 대비 마스크가 성기거나 불규칙하면
  진짜 타깃 영역을 짚어내기 어렵다는 뜻이므로 scarcity가 높아진다.
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


def _squash(value: float, k: float) -> float:
    return float(value / (value + k)) if value > 0 else 0.0


def _texture_variance(rgb: NDArray[np.uint8]) -> float:
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
