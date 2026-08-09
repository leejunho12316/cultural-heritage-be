"""Geometry metrics used by relation authority."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING

import numpy as np

from modules.anomaly_grouping.models import BoundingBox
from modules.shared import ContractValidationError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from numpy.typing import NDArray

    from modules.anomaly_grouping.models import MaskReference

_POLYGON_EPSILON_RATIO = 0.01  # cv2.approxPolyDP tolerance, as a fraction of perimeter


# 두 박스의 교차 영역을 계산하는 기본 연산. overlaps가 이 위에서 파생된다.
def intersection_area(left: BoundingBox, right: BoundingBox) -> float:
    """Return the overlapping bbox area in source units."""
    width = max(0.0, min(left.x_max, right.x_max) - max(left.x_min, right.x_min))
    height = max(0.0, min(left.y_max, right.y_max) - max(left.y_min, right.y_min))
    return width * height


# relations.py에서 두 후보를 마스크 정밀 비교 대상으로 삼을지 가르는 값싼 1차
# 필터로만 쓰인다. 실제 병합 여부·관계 세분류는 아래 mask_* 함수(마스크 픽셀
# 기준)가 담당한다 - bbox는 어디까지나 보조 신호다.
def overlaps(left: BoundingBox, right: BoundingBox) -> bool:
    """Return whether two boxes have positive intersection area (cheap prefilter)."""
    return intersection_area(left, right) > 0.0


# 아래 mask_* 함수들이 공통으로 쓰는 내부 로더. 마스크 PNG를 불리언 전경
# 배열로 읽어들인다. PIL은 모듈 최상단이 아니라 여기서 지역 import한다 -
# anomaly_grouping을 그냥 import만 하는 경로(예: 스테이지 계약 모듈)가 실제
# 마스크를 읽지도 않으면서 PIL을 강제로 로드하지 않게 하기 위함이다.
def load_mask_array(mask: MaskReference) -> NDArray[np.bool_]:
    """Load one mask PNG as a boolean foreground array."""
    from PIL import Image  # noqa: PLC0415

    with Image.open(mask.path) as image:
        return np.asarray(image.convert("L")) > 0


# relations.py의 _parent_child가 두 후보 중 마스크 픽셀 수가 더 많은 쪽을
# parent로 고를 때 쓴다 - 이 모듈의 병합 판정 전체가 bbox가 아니라 마스크를
# 기준으로 하므로, parent/child 선택도 bbox 면적이 아니라 마스크 픽셀 수로
# 맞춰야 containment/area_ratio 계산 방향이 실제 분류 기준과 일치한다.
def mask_pixel_count(mask: MaskReference) -> int:
    """Return the number of foreground pixels in one mask."""
    return int(np.count_nonzero(load_mask_array(mask)))


# relations.py의 _classify에서 SAME_ANOMALY_DUPLICATE 판정 기준(마스크 IoU
# 임계값)으로 쓰인다.
def mask_iou(left: MaskReference, right: MaskReference) -> float:
    """Return pixel intersection-over-union for two masks."""
    left_array = load_mask_array(left)
    right_array = load_mask_array(right)
    intersection = int(np.count_nonzero(left_array & right_array))
    union = int(np.count_nonzero(left_array | right_array))
    if union == 0:
        return 0.0
    return intersection / union


# relations.py의 _classify에서 SAME_ANOMALY_REFINEMENT 판정에 mask_area_ratio와
# 함께 쓰이며, 자식 마스크가 부모 마스크 안에 얼마나 포함되는지를 본다.
def mask_containment(child: MaskReference, parent: MaskReference) -> float:
    """Return the fraction of the child mask's pixels covered by the parent mask."""
    child_array = load_mask_array(child)
    child_area = int(np.count_nonzero(child_array))
    if child_area == 0:
        return 0.0
    parent_array = load_mask_array(parent)
    intersection = int(np.count_nonzero(child_array & parent_array))
    return intersection / child_area


# mask_containment와 짝을 이뤄 REFINEMENT 판정에 쓰이며, 자식 마스크가
# 부모보다 픽셀 수 기준으로 충분히 작은 하위 영역인지 확인한다.
def mask_area_ratio(child: MaskReference, parent: MaskReference) -> float:
    """Return child mask pixel count divided by parent mask pixel count."""
    parent_area = int(np.count_nonzero(load_mask_array(parent)))
    if parent_area == 0:
        return 0.0
    child_area = int(np.count_nonzero(load_mask_array(child)))
    return child_area / parent_area


# relation_results.py가 병합이 확정된 후보 그룹의 최종 마스크를 만들 때
# 호출한다. 그룹 내 모든 마스크의 픽셀 합집합을 계산한다 - "대표 하나 선택"이
# 아니라 병합된 전체를 다음 단계로 넘기는 것이 목표다.
def mask_union_array(masks: Sequence[MaskReference]) -> NDArray[np.bool_]:
    """Return the pixel union of all given masks."""
    arrays = [load_mask_array(mask) for mask in masks]
    union = arrays[0]
    for array in arrays[1:]:
        union = union | array
    return union


# relation_results.py에서 병합된 마스크의 bbox를 파생시킬 때 호출한다. bbox는
# 여전히 표시/추적 보조용으로 남아있지만, 이제 마스크에서 파생된 값일 뿐
# 독립적인 기준이 아니다.
def mask_bbox(array: NDArray[np.bool_]) -> BoundingBox:
    """Return the tight bounding box of a non-empty boolean mask array."""
    rows = np.nonzero(np.any(array, axis=1))[0]
    cols = np.nonzero(np.any(array, axis=0))[0]
    if rows.size == 0 or cols.size == 0:
        field = "mask"
        reason = "mask_bbox requires a mask with at least one foreground pixel"
        raise ContractValidationError(field, reason)
    y_min, y_max = int(rows[0]), int(rows[-1])
    x_min, x_max = int(cols[0]), int(cols[-1])
    return BoundingBox(
        float(x_min), float(y_min), float(x_max) + 1.0, float(y_max) + 1.0
    )


# relation_results.py가 병합 마스크를 디스크에 기록할 때 호출한다. 0/255
# 흑백 PNG로 저장하고 내용 해시를 반환해 MaskReference를 만들 수 있게 한다.
def write_mask_png(array: NDArray[np.bool_], path: Path) -> str:
    """Write a boolean mask array as a PNG and return its sha256 digest."""
    from PIL import Image  # noqa: PLC0415

    path.parent.mkdir(parents=True, exist_ok=True)
    mask_bytes: NDArray[np.uint8] = np.multiply(array, 255).astype(np.uint8)
    Image.fromarray(mask_bytes).save(path)
    return sha256(path.read_bytes()).hexdigest()


_MIN_POLYGON_POINTS = 3

# relation_results.py가 최종 kept 후보(단독이든 병합 union이든)마다 호출한다.
# 마스크 윤곽선을 폴리곤 좌표들로 벡터화한다 - FE가 실제 세그멘테이션 모양을
# 그릴 수 있도록 리포트 JSON에 실어 나르는 표시용 값이다(기준은 여전히
# 래스터 마스크). 실측 SAM2 마스크로 확인해보니 흩어진 손상(예: 점무늬 부식)은
# 연결 성분이 여러 개인 경우가 흔해서, 가장 큰 성분 하나만 남기면 나머지
# 조각의 면적이 통째로 사라진다(예: 표본 하나는 최대 성분이 전체 면적의 19%뿐
# 이었다) - 그래서 성분마다 폴리곤을 따로 반환한다. cv2는 여기서만 지역
# import한다(다른 mask_* 함수들의 PIL과 같은 이유 - anomaly_grouping을 그냥
# import만 하는 경로가 무거운 이미지 러타임을 강제로 로드하지 않게 함).
def mask_polygons(
    array: NDArray[np.bool_],
) -> tuple[tuple[tuple[float, float], ...], ...]:
    """Return one simplified polygon outline per connected foreground component."""
    import cv2  # noqa: PLC0415

    mask_bytes: NDArray[np.uint8] = np.multiply(array, 255).astype(np.uint8)
    contours, _ = cv2.findContours(
        mask_bytes, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    polygons = (_contour_polygon(contour) for contour in contours)
    valid = (polygon for polygon in polygons if len(polygon) >= _MIN_POLYGON_POINTS)
    return tuple(sorted(valid, key=_polygon_area, reverse=True))


# mask_polygons에서 호출된다. 단순화 후 점이 3개 미만으로 쪼그라들면(아주 작은
# 성분에서 흔함) 단순화를 건너뛰고 원본 컨투어 점을 그대로 쓴다 - 근사가
# 폴리곤을 점 1~2개로 무너뜨려 화면에서 사실상 안 보이게 되는 것을 막는다.
def _contour_polygon(contour: NDArray[np.int32]) -> tuple[tuple[float, float], ...]:
    import cv2  # noqa: PLC0415

    perimeter = cv2.arcLength(contour, closed=True)
    epsilon = max(1.0, _POLYGON_EPSILON_RATIO * perimeter)
    simplified = cv2.approxPolyDP(contour, epsilon, closed=True)
    points = tuple((float(point[0][0]), float(point[0][1])) for point in simplified)
    if len(points) >= _MIN_POLYGON_POINTS:
        return points
    return tuple((float(point[0][0]), float(point[0][1])) for point in contour)


def _polygon_area(polygon: tuple[tuple[float, float], ...]) -> float:
    import cv2  # noqa: PLC0415

    points = np.array(polygon, dtype=np.float32).reshape(-1, 1, 2)
    return float(cv2.contourArea(points))
