"""Pure path/array-based mask pixel operations shared across pipeline stages.

Extracted from `modules.anomaly_grouping.geometry` so that upstream stages
(e.g. `rough_masking`) can union/write mask pixels without importing a
downstream stage module. These functions take plain `Path`s and arrays only -
no dependency on any stage's candidate/mask-reference dataclasses.
"""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING

import numpy as np

from modules.shared.errors import ContractValidationError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from numpy.typing import NDArray


# 마스크 PNG를 불리언 전경 배열로 읽어들인다. PIL은 모듈 최상단이 아니라
# 여기서 지역 import한다 - modules.shared를 그냥 import만 하는 경로가 실제
# 마스크를 읽지도 않으면서 PIL을 강제로 로드하지 않게 하기 위함이다.
def load_mask_array(path: Path) -> NDArray[np.bool_]:
    """Load one mask PNG as a boolean foreground array."""
    from PIL import Image  # noqa: PLC0415

    with Image.open(path) as image:
        return np.asarray(image.convert("L")) > 0


# 여러 마스크 파일의 픽셀 합집합을 계산한다. "대표 하나 선택"이 아니라
# 병합된 전체를 다음 단계로 넘기는 것이 목표다.
def mask_union_array(paths: Sequence[Path]) -> NDArray[np.bool_]:
    """Return the pixel union of all given mask files."""
    arrays = [load_mask_array(path) for path in paths]
    union = arrays[0]
    for array in arrays[1:]:
        union = union | array
    return union


# 서로 다른 뷰(객체 크롭/타일)에서 나온, 각자 자기 뷰 기준 로컬 좌표인
# 마스크들을 공통 캔버스(전달된 xyxy들의 합집합 bbox) 위에 정렬해서 합친다.
# 단순히 배열끼리 union|array로 겹치면 안 되는 이유: 각 마스크는 서로 다른
# 원점과 (부동소수점 타일 크기 계산 때문에) 미세하게 다른 크기를 가질 수
# 있어서, 그대로 겹치면 크기가 안 맞아 죽거나(모양 다름) 죽지 않아도 잘못된
# 위치에 겹쳐진다. anomaly_grouping의 사전(pre-refinement) 병합
# (pre_refinement_merge.py)에서 쓴다 - 원본 이미지 좌표로 이미 복원된
# 마스크들을 합치는 것이라 각자의 원점이 다르다.
def mask_union_at_boxes(
    masks_and_boxes: Sequence[
        tuple[NDArray[np.bool_], tuple[float, float, float, float]]
    ],
) -> tuple[NDArray[np.bool_], tuple[float, float, float, float]]:
    """Union crop-local masks by placing each at its own xyxy box on a shared canvas."""
    if not masks_and_boxes:
        field = "masks_and_boxes"
        reason = "mask_union_at_boxes requires at least one mask/box pair"
        raise ContractValidationError(field, reason)
    canvas_left = min(round(box[0]) for _, box in masks_and_boxes)
    canvas_top = min(round(box[1]) for _, box in masks_and_boxes)
    canvas_right = max(round(box[2]) for _, box in masks_and_boxes)
    canvas_bottom = max(round(box[3]) for _, box in masks_and_boxes)
    canvas = np.zeros(
        (canvas_bottom - canvas_top, canvas_right - canvas_left), dtype=np.bool_
    )
    for mask, box in masks_and_boxes:
        offset_x = round(box[0]) - canvas_left
        offset_y = round(box[1]) - canvas_top
        height = min(mask.shape[0], canvas.shape[0] - offset_y)
        width = min(mask.shape[1], canvas.shape[1] - offset_x)
        canvas[offset_y : offset_y + height, offset_x : offset_x + width] |= mask[
            :height, :width
        ]
    return canvas, (
        float(canvas_left),
        float(canvas_top),
        float(canvas_right),
        float(canvas_bottom),
    )


# 병합된 마스크의 bbox를 파생시킬 때 호출한다. xyxy 튜플만 돌려주고 어떤
# BoundingBox 타입으로 감쌀지는 호출부(스테이지별 모델)가 정한다.
def array_bbox(array: NDArray[np.bool_]) -> tuple[float, float, float, float]:
    """Return the tight xyxy bounding box of a non-empty boolean mask array."""
    rows = np.nonzero(np.any(array, axis=1))[0]
    cols = np.nonzero(np.any(array, axis=0))[0]
    if rows.size == 0 or cols.size == 0:
        field = "mask"
        reason = "array_bbox requires a mask with at least one foreground pixel"
        raise ContractValidationError(field, reason)
    y_min, y_max = int(rows[0]), int(rows[-1])
    x_min, x_max = int(cols[0]), int(cols[-1])
    return (float(x_min), float(y_min), float(x_max) + 1.0, float(y_max) + 1.0)


# 병합 마스크를 디스크에 기록한다. 0/255 흑백 PNG로 저장하고 내용 해시를
# 반환해 호출부가 자기 스테이지의 마스크 참조 타입을 만들 수 있게 한다.
def write_mask_png(array: NDArray[np.bool_], path: Path) -> str:
    """Write a boolean mask array as a PNG and return its sha256 digest."""
    from PIL import Image  # noqa: PLC0415

    path.parent.mkdir(parents=True, exist_ok=True)
    mask_bytes: NDArray[np.uint8] = np.multiply(array, 255).astype(np.uint8)
    Image.fromarray(mask_bytes).save(path)
    return sha256(path.read_bytes()).hexdigest()
