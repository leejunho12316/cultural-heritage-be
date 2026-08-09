"""Mask helpers for preprocessing materialized assets."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from modules.preprocessing.assets.mask_components import MaskComponent


# bool 배열을 0/255 흑백 PNG로 변환하고 원본 이미지 크기로 리사이즈한다.
# 마스크 경계가 흐려지지 않도록 NEAREST 보간을 사용한다.
# component_mask_image에서 호출된다.
def _mask_image(
    mask_array: NDArray[np.bool_], source_size: tuple[int, int]
) -> Image.Image:
    mask_bytes: NDArray[np.uint8] = np.multiply(mask_array, 255).astype(np.uint8)
    return Image.fromarray(mask_bytes).resize(source_size, Image.Resampling.NEAREST)


def component_mask_image(
    component: MaskComponent,
    image_size: tuple[int, int],
    crop_box: tuple[int, int, int, int],
) -> Image.Image:
    """Return the source-sized foreground component mask image."""
    component_mask = _full_component_mask_array(component, image_size, crop_box)
    return _mask_image(component_mask, image_size)


# crop_box 내부 좌표계인 컴포넌트 마스크를 crop_box의 offset만큼 이동시켜
# 원본 이미지 크기의 전체 마스크 배열에 붙여넣는다. component_mask_image에서 호출된다.
def _full_component_mask_array(
    component: MaskComponent,
    image_size: tuple[int, int],
    crop_box: tuple[int, int, int, int],
) -> NDArray[np.bool_]:
    image_width, image_height = image_size
    left, top, _, _ = crop_box
    full_mask = np.zeros((image_height, image_width), dtype=np.bool_)
    component_height = len(component.mask)
    component_width = len(component.mask.T)
    full_mask[top : top + component_height, left : left + component_width] = (
        component.mask
    )
    return full_mask
