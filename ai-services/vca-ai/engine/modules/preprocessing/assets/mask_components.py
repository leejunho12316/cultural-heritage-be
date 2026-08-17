"""Connected components for boolean SAM masks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from numpy.typing import NDArray

WHITE_BACKGROUND_THRESHOLD = 245


@dataclass(frozen=True, slots=True)
class MaskComponent:
    """One connected foreground region in a mask."""

    mask: NDArray[np.bool_]
    bbox_xyxy: tuple[int, int, int, int]
    area_px: int


def connected_mask_components(
    mask: NDArray[np.bool_], min_area_px: int = 1
) -> tuple[MaskComponent, ...]:
    """Return 8-connected foreground components sorted by reading order."""
    height = len(mask)
    width = len(mask.T)
    visited = np.zeros(mask.shape, dtype=np.bool_)
    components: list[MaskComponent] = []
    for y in range(height):
        for x in range(width):
            if visited[y, x] or not mask[y, x]:
                continue
            pixels = _collect_component(mask, visited, x, y)
            if len(pixels) < min_area_px:
                continue
            component_mask = np.zeros(mask.shape, dtype=np.bool_)
            xs = [pixel[0] for pixel in pixels]
            ys = [pixel[1] for pixel in pixels]
            component_mask[ys, xs] = True
            components.append(
                MaskComponent(
                    mask=component_mask,
                    bbox_xyxy=(min(xs), min(ys), max(xs) + 1, max(ys) + 1),
                    area_px=len(pixels),
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


# 시작 픽셀에서부터 8방향 flood-fill로 연결된 전경 픽셀 좌표들을 모은다.
# connected_mask_components에서 아직 방문하지 않은 전경 픽셀을 만날 때마다 호출된다.
def _collect_component(
    mask: NDArray[np.bool_], visited: NDArray[np.bool_], start_x: int, start_y: int
) -> list[tuple[int, int]]:
    height = len(mask)
    width = len(mask.T)
    stack = [(start_x, start_y)]
    pixels: list[tuple[int, int]] = []
    visited[start_y, start_x] = True
    while stack:
        x, y = stack.pop()
        pixels.append((x, y))
        for neighbor_y in range(max(y - 1, 0), min(y + 2, height)):
            for neighbor_x in range(max(x - 1, 0), min(x + 2, width)):
                if visited[neighbor_y, neighbor_x] or not mask[neighbor_y, neighbor_x]:
                    continue
                visited[neighbor_y, neighbor_x] = True
                stack.append((neighbor_x, neighbor_y))
    return pixels
