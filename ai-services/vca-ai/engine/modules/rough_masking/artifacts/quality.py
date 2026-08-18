"""Mask quality scoring for clipped rough-mask candidates."""

# ruff: noqa: PLC0415
# pyright: reportArgumentType=false, reportGeneralTypeIssues=false, reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false

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
    mask: NDArray[np.bool_],
    object_foreground: NDArray[np.bool_],
    *,
    apply_area_quality_gates: bool = True,
) -> _QualityDecision:
    """Score one clipped mask and decide whether v1 should keep it.

    `apply_area_quality_gates=False` still computes every metric (for
    reporting) but never rejects on area_ratio/bbox_fill_ratio/
    boundary_pixel_ratio - those thresholds assume the mask sits inside a
    whole-object ROI, and misfire against a tight candidate-centered crop
    where a real anomaly can legitimately fill most of the ROI.
    """
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
    reject_reason = (
        _reject_reason(quality, area_px) if apply_area_quality_gates else None
    )
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


# 객체 전경 마스크에서 침식(erosion) 차분으로 경계 픽셀을 얻고,
# ROI 크롭 테두리로 인한 가짜 경계는 _without_roi_frame으로 제거한다.
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
    # erosion은 바깥쪽을 False로 패딩하므로, 크롭 테두리에 닿은 전경 픽셀은
    # 실제 객체 경계와 무관해도 항상 boundary로 표시된다. 이 테두리를 제거하지
    # 않으면 boundary_pixel_ratio와 perimeter_coverage_ratio가 ROI 크롭 자체를
    # 이상 부위로 과대 집계하게 된다.
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


# 마스크의 연결 요소 개수와 최대 요소의 면적 비율을 계산한다.
# 파편화(fragmentation) 정도를 판단하는 데 쓰이며 _quality_score의 입력이 된다.
# scipy.ndimage.label의 기본 structure(십자형, 4-이웃)가 예전 순수 Python
# flood-fill의 상하좌우 전용 이웃 판정과 동일해 결과가 그대로 유지된다 -
# 오브젝트 크롭처럼 수백만 픽셀짜리 마스크에서 Python 루프 flood-fill이
# 병목이었던 걸 C로 컴파일된 벡터 연산으로 대체한다.
def _component_stats(mask: NDArray[np.bool_], area_px: int) -> tuple[int, float]:
    import numpy as np
    from scipy import ndimage

    labeled, component_count = ndimage.label(mask)
    if component_count == 0:
        return 0, 0.0
    counts = np.bincount(labeled.ravel())
    largest_component_px = int(counts[1:].max())
    return component_count, float(largest_component_px) / float(area_px)


# 면적/경계/파편화 페널티를 가중합하여 0~1 품질 점수를 만든다.
# 가중치(0.25/0.35/0.20)는 잠금값이며 조정 시 합격 기준 전체가 바뀐다.
def _quality_score(
    area_ratio: float, boundary_pixel_ratio: float, largest_component_ratio: float
) -> float:
    area_penalty = min(area_ratio / _BROAD_BLOB_MIN_AREA_RATIO, 1.0) * 0.25
    boundary_penalty = min(boundary_pixel_ratio, 1.0) * 0.35
    fragmentation_penalty = (1.0 - largest_component_ratio) * 0.20
    return max(0.0, 1.0 - area_penalty - boundary_penalty - fragmentation_penalty)


# 모듈 상단의 임계값들로 broad_texture_blob/boundary_only/edge_artifact 여부를
# 판정한다. 어떤 rough candidate가 살아남는지를 최종 결정하는 함수다.
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
