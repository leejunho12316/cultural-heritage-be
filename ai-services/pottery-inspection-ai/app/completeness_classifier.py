"""
completeness_classifier.py

도자기가 "완전한 형태"인지 "파편"인지를 외곽선 형태로 1차 판별하는
시작 코드.

접근 방식
---------
완전한 그릇은 보통 좌우 대칭에 가깝고, 외곽선이 매끄러운 곡선
(입구-몸통-굽으로 이어지는 연속된 윤곽)을 이룬다. 파편은:
- 최소 한쪽 이상에 "깨진 단면"에 해당하는 날카롭고 불규칙한
  직선/톱니형 경계가 있다
- 좌우 대칭성이 깨져 있는 경우가 많다
- 원형/타원형에 가까운 완전한 그릇 입구(원형 rim)와 달리,
  파편의 절단면은 볼록/오목이 불규칙하게 섞여 있다

그래서 다음 특징을 뽑는다.
1. 대칭성 점수: 마스크를 무게중심 기준 좌우 반전해서 IoU 비교
2. 외곽선 곡률 변화율: convexity defects(오목한 부분) 개수와 깊이 -
   파편의 절단면은 국소적으로 깊고 날카로운 defect를 만드는 경향
3. 원형도(circularity): 4*pi*area/perimeter^2 - 완전한 원형 그릇
   입구나 몸통 실루엣은 원/타원에 가까워 원형도가 높은 편

주의
----
이것도 규칙 기반 1차 스코어일 뿐이다. 그릇 종류(항아리/접시/병 등)
마다 정상적인 실루엣 자체가 다르므로, 이 스코어 하나로 "완전/파편"을
확정하면 안 되고, VLM이나 사람 검토와 함께 "판단 보류" 후보를
넉넉히 남기는 방향으로 써야 한다 (부식 프로젝트의 uncertain 클래스와
같은 개념).

사용법:
    from completeness_classifier import estimate_completeness
    result = estimate_completeness(object_mask)
"""

from dataclasses import dataclass

import cv2
import numpy as np

THRESHOLDS = {
    "symmetry_iou_min": 0.7,  # 이 이상이면 "대칭적" 후보
    "circularity_min": 0.55,  # 이 이상이면 "매끄러운 실루엣" 후보
    "sharp_defect_depth_ratio_max": 0.08,  # 마스크 대각선 대비 defect 깊이
}


@dataclass
class CompletenessEstimate:
    guess: str  # "완전" | "파편" | "판단 보류"
    symmetry_iou: float
    circularity: float
    max_defect_depth_ratio: float
    note: str


def _largest_contour(binary_mask: np.ndarray):
    contours, _ = cv2.findContours(
        binary_mask.astype(np.uint8),
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    if not contours:
        return None
    return max(contours, key=cv2.contourArea)


def _symmetry_iou(binary_mask: np.ndarray) -> float:
    ys, xs = np.where(binary_mask)
    center_x = int(round(xs.mean()))

    flipped = np.zeros_like(binary_mask)
    height, width = binary_mask.shape
    # 무게중심 기준 좌우 반전 (이미지 경계를 벗어나는 부분은 무시)
    for y, x in zip(ys, xs):
        mirrored_x = 2 * center_x - x
        if 0 <= mirrored_x < width:
            flipped[y, mirrored_x] = True

    intersection = np.logical_and(binary_mask, flipped).sum()
    union = np.logical_or(binary_mask, flipped).sum()
    return float(intersection / union) if union > 0 else 0.0


def _circularity(contour, area: float) -> float:
    perimeter = cv2.arcLength(contour, True)
    if perimeter == 0:
        return 0.0
    return float(4 * np.pi * area / (perimeter ** 2))


def _max_defect_depth_ratio(contour, binary_mask: np.ndarray) -> float:
    hull = cv2.convexHull(contour, returnPoints=False)
    if hull is None or len(hull) < 3:
        return 0.0
    try:
        defects = cv2.convexityDefects(contour, hull)
    except cv2.error:
        return 0.0
    if defects is None or len(defects) == 0:
        return 0.0

    diagonal = float(np.hypot(*binary_mask.shape))

    # cv2.convexityDefects는 보통 (n, 1, 4) 모양을 돌려주는데,
    # OpenCV/numpy 버전 조합에 따라 (n, 4)로 나오는 경우가 있어서
    # d[0][3]이 "스칼라를 인덱싱하려 함" 에러를 낼 수 있었다.
    # reshape(-1, 4)로 모양을 강제로 통일해서 버전에 상관없이 동작하게 한다.
    defects_array = np.asarray(defects).reshape(-1, 4)
    max_depth = float(defects_array[:, 3].max()) / 256.0  # fixed-point depth
    return max_depth / diagonal if diagonal > 0 else 0.0


def estimate_completeness(object_mask: np.ndarray) -> CompletenessEstimate:
    binary_mask = np.asarray(object_mask, dtype=bool)
    contour = _largest_contour(binary_mask)

    if contour is None or cv2.contourArea(contour) == 0:
        return CompletenessEstimate(
            guess="판단 보류",
            symmetry_iou=0.0,
            circularity=0.0,
            max_defect_depth_ratio=0.0,
            note="윤곽선을 추출하지 못했습니다.",
        )

    area = cv2.contourArea(contour)
    symmetry_iou = _symmetry_iou(binary_mask)
    circularity = _circularity(contour, area)
    defect_ratio = _max_defect_depth_ratio(contour, binary_mask)

    is_symmetric = symmetry_iou >= THRESHOLDS["symmetry_iou_min"]
    is_smooth = circularity >= THRESHOLDS["circularity_min"]
    has_sharp_defect = defect_ratio > THRESHOLDS["sharp_defect_depth_ratio_max"]

    if is_symmetric and is_smooth and not has_sharp_defect:
        guess = "완전"
    elif has_sharp_defect and not is_symmetric:
        guess = "파편"
    else:
        guess = "판단 보류"

    return CompletenessEstimate(
        guess=guess,
        symmetry_iou=round(symmetry_iou, 3),
        circularity=round(circularity, 3),
        max_defect_depth_ratio=round(defect_ratio, 4),
        note=(
            "규칙 기반 잠정 추정치입니다. 그릇 종류(항아리/접시/병 등)에 "
            "따라 정상 실루엣 자체가 달라 오판 가능성이 있으니, "
            "'판단 보류'는 반드시 사람이 확인해야 합니다."
        ),
    )
