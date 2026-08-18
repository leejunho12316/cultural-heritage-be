"""Connected components for boolean SAM masks."""

# ruff: noqa: PLC0415
# pyright: reportAny=false, reportArgumentType=false, reportGeneralTypeIssues=false, reportMissingTypeStubs=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from numpy.typing import NDArray

WHITE_BACKGROUND_THRESHOLD = 245
# 8-연결(대각선 포함) 구조 요소 - 예전 수동 flood-fill이 3x3 이웃 전체를
# 검사하던 것과 동일한 연결 규칙을 scipy.ndimage.label에 지정한다.
_EIGHT_CONNECTED_STRUCTURE: tuple[tuple[int, int, int], ...] = (
    (1, 1, 1),
    (1, 1, 1),
    (1, 1, 1),
)


@dataclass(frozen=True, slots=True)
class MaskComponent:
    """One connected foreground region in a mask."""

    mask: NDArray[np.bool_]
    bbox_xyxy: tuple[int, int, int, int]
    area_px: int


# 예전에는 순수 Python 8방향 flood-fill(_collect_component, 전경 픽셀 하나당
# 함수 호출 1회)로 연결요소를 모았다 - 오브젝트 크롭이 크고 흰 배경 가정이
# 안 맞는 사진(돌·나무·직물 배경)에서는 전경 픽셀 수가 수백만까지 갈 수 있어
# 그만큼 느려졌다(실측: 2500x2500, 전경 30%에서 OOM으로 프로세스 강제 종료).
# scipy.ndimage.label로 대체하되, 라벨마다 전체 배열과 비교(O(컴포넌트 수 x
# 전체 픽셀 수))하지 않도록 find_objects로 각 라벨의 바운딩 박스 슬라이스만
# 받아 그 안에서만 비교한다 - 노이즈가 심해 라벨이 수십만 개로 쪼개지는
# 입력에서도 min_area_px 미만인 라벨은애초에 마스크를 만들지 않고 걸러진다.
def connected_mask_components(
    mask: NDArray[np.bool_], min_area_px: int = 1
) -> tuple[MaskComponent, ...]:
    """Return 8-connected foreground components sorted by reading order."""
    from scipy import ndimage

    labeled, component_count = ndimage.label(
        mask, structure=np.asarray(_EIGHT_CONNECTED_STRUCTURE)
    )
    if component_count == 0:
        return ()
    areas = np.bincount(labeled.ravel(), minlength=component_count + 1)
    slices = ndimage.find_objects(labeled)
    components: list[MaskComponent] = []
    for label in range(1, component_count + 1):
        area_px = int(areas[label])
        if area_px < min_area_px:
            continue
        row_slice, col_slice = slices[label - 1]
        component_mask = np.zeros(mask.shape, dtype=np.bool_)
        component_mask[row_slice, col_slice] = labeled[row_slice, col_slice] == label
        components.append(
            MaskComponent(
                mask=component_mask,
                bbox_xyxy=(
                    col_slice.start,
                    row_slice.start,
                    col_slice.stop,
                    row_slice.stop,
                ),
                area_px=area_px,
            )
        )
    return tuple(
        sorted(
            components,
            key=lambda component: (
                component.bbox_xyxy[1],
                component.bbox_xyxy[0],
                -component.area_px,
            ),
        )
    )


def foreground_mask_from_rgb(
    rgb: NDArray[np.uint8], white_threshold: int = WHITE_BACKGROUND_THRESHOLD
) -> NDArray[np.bool_]:
    """Return foreground pixels under the white-background assumption."""
    return np.any(rgb < white_threshold, axis=2)
