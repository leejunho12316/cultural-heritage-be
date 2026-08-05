"""
precise_pattern_masking.py

문양 범위별 시각화 전략 (v4):

- small_instance: 점 주변 국소 창에서, 점이 속하거나 가장 가까운 잉크
  연결요소를 그대로 문양 경계로 쓴다. 결과가 국소 창/유물 대비 너무
  크면(SMALL_INSTANCE_MAX_ARTIFACT_RATIO 초과) "이건 작은 개별 문양이
  아니라 주변과 이어진 큰 덩어리"로 보고 실패시켜 근사 박스로 폴백한다.
  (SAM2 point prompt는 실사용 테스트에서 대부분 실패해서 뺐다)
- large_continuous: VLM bbox ∩ 유물 실루엣에서, 이미 확정된 다른 문양
  (exclusion_mask)을 뺀 나머지를 그대로 영역으로 쓴다. 잉크 연결요소로
  정밀하게 선묘를 따라가려는 시도를 세 번 반복했지만 (전체 팽창 ->
  연결요소 선택 -> 실패한 이웃 문양 제외) 매번 "다른 문양 영역을
  침범한다"는 문제가 형태만 바뀌어 재발했다. 실제 요구사항은 픽셀 단위
  정밀도가 아니라 "여기가 용 영역이라는 걸 알 수 있으면서 다른 문양은
  침범하지 않는 것"이었으므로, bbox 기반의 더 단순하고 예측 가능한
  집합 연산(빼기)으로 바꿨다.
- border_band: 대략적인 상/하/중단 탐색 구간에서 행(row)별 잉크 밀도로
  띠의 세로 범위를 먼저 찾고, 그 구간의 전체 폭을 채우는 대신 실제
  잉크 픽셀만 남긴다 (스캘럽/물결 문양 사이 빈 공간은 제외).

border_band/small_instance는 실제 안료 픽셀을 따라가고, large_continuous는
bbox에서 다른 문양 영역만 뺀 넉넉한 영역을 쓴다 - 문양마다 "정밀함"과
"다른 문양을 침범하지 않는 안정성" 중 실제로 더 중요한 쪽을 우선했다.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from models.grounded_sam_detector import GroundedSAMDetector
from pottery_pattern_vlm_locator import PatternLocation, analyze_pottery_patterns_ensemble

POTTERY_TEXT_PROMPT = "ceramic vessel. pottery."

BADGE_COLORS = [
    (230, 25, 75),
    (60, 180, 75),
    (0, 130, 200),
    (245, 130, 48),
    (145, 30, 180),
    (70, 240, 240),
    (240, 50, 230),
    (210, 245, 60),
]

# 색상(잉크) 기반 마스킹 파라미터.
MIN_BACKGROUND_DISTANCE = 10.0  # Lab 색공간 거리 임계값 하한 (노이즈만으로 전체가 안 걸리게)
INK_BRIDGE_KERNEL = 3  # 붓선/앤티앨리어싱 때문에 끊긴 픽셀만 이어붙이는 최소 팽창.
# large_continuous에서 예전에 9였다가 5로 줄였는데, 실사용 테스트에서도
# 여전히 근처의 분리된 구름 장식까지 같이 이어붙는 문제가 있었다. 지금은
# "덩어리를 크게 만드는" 역할을 팽창이 아니라 연결요소 선택이 하도록
# 바꿨으므로, 팽창은 정말 미세한 끊김만 메우면 되는 최소값(3)으로 낮췄다.
BAND_DENSE_ROW_FLOOR = 0.02  # 띠로 인정할 최소 잉크 밀도(행 단위). Otsu 결과가
# 너무 낮게 잡히는 경우(잉크가 거의 없을 때)를 대비한 하한선.
BAND_ROW_PADDING_RATIO = 0.01  # 밀집 구간 앞뒤로 살짝 여유를 주는 비율(유물 높이 대비)
SMALL_INSTANCE_WINDOW_RATIO = 0.08  # 점 주변에서 연결요소를 찾는 국소 창 크기(이미지 대비).
# 실사용 테스트에서 0.15였을 때, 장식이 빽빽한 구역(용머리 주변 등)에서는
# 국소 창 안의 잉크가 서로 다 이어져 있어 "작은 문양 하나"가 아니라
# 창 전체에 가까운 거대한 연결요소가 잡히는 문제가 있었다. 창을 줄이고,
# 아래 SMALL_INSTANCE_MAX_ARTIFACT_RATIO로 결과 크기도 한 번 더 검증한다.
SMALL_INSTANCE_MAX_ARTIFACT_RATIO = 0.06  # 이보다 크면 "작은 개별 문양"이 아니라고
# 보고 실패시켜(근사 박스로 폴백) 큰 덩어리를 잘못 작은 문양으로 표시하는 걸 막는다.
SMALL_INSTANCE_MAX_WINDOW_FILL_RATIO = 0.55  # 결과 연결요소가 국소 창(window) 자체의
# 잉크 가능 면적 대비 이보다 크게 차지하면 실패시킨다. 위 ARTIFACT_RATIO만으로는
# 못 잡는 경우가 있었다 - 창이 이미지 대비 8%로 작다 보니, 점이 사실 띠 문양처럼
# 훨씬 큰 덩어리에 속해 있어도 창 경계에서 잘려나간 결과의 "유물 전체 대비 면적"은
# 여전히 6% 밑으로 작게 나올 수 있었다(실사용 테스트: 상단 띠 근처 구름 점이
# 띠 잉크와 이어져 있었는데, 창에 잘린 사각형 조각이 유물 대비로는 3%라 통과해서
# 화면에 각진 사각 블록으로 나타남). 창 자체를 거의 다 채운다는 건 "이 창 안에서는
# 경계를 못 찾았다(잘려나갔다)"는 신호이므로, 창 대비 비율도 별도로 확인한다.
DENSITY_BLUR_SIGMA = 14.0  # large_continuous 소프트 알파(밀도) 맵을 만들 때 쓰는
# 가우시안 블러 강도. bbox 영역(roi) 자체는 안전한 경계로 그대로 두되,
# 화면에 칠하는 알파를 실제 잉크가 몰린 곳일수록 진하게, 빈 유약 부분은
# 옅게 보이도록 해서 "정밀하게 문양을 따라가는 것처럼" 보이게 한다.
# 해상도에 따라 값을 조정할 필요가 있을 수 있다.

# v10 수정: build_border_band_mask는 세로 범위는 잉크 밀도로 좁히면서 가로는
# 유물 폭 전체를 그대로 훑었다. 실사용 테스트(용문 도자기)에서 용 머리가
# 상단 띠와 비슷한 높이까지 올라와 있어, 같은 청색 잉크가 상단 띠 마스크에
# 함께 섞여 들어가는 문제가 확인됐다. VLM이 각 문양마다 bbox_percent(가로
# 범위 포함)를 이미 주므로, 이를 가로 탐색 범위의 출발점으로 쓴다.
BAND_HORIZONTAL_PAD_RATIO = 0.10  # VLM bbox 가로 폭의 좌우 여유(패딩) 비율.
# bbox는 정확한 픽셀 경계가 아니라 근사치이므로 그대로 자르면 띠의 양 끝이나
# 곡면 부분이 잘릴 수 있어, 폭의 10%만큼 좌우로 여유를 더 준다.
BAND_MIN_WIDTH_RATIO = 0.65  # 가로 ROI가 아무리 좁아도 유물 폭 대비 최소 이만큼은
# 확보한다(중심 유지, 대칭 확장). 띠 문양은 보통 유물 둘레를 넓게 두르므로,
# VLM bbox가 사진에 보이는 앞면 일부만 좁게 잡았더라도 과도하게 잘리지 않게 한다.
BAND_RESTRICTED_MIN_RATIO = 0.5  # 가로 ROI로 제한했을 때 남는 잉크 픽셀 수가 제한
# 전(유물 전체 폭 기준) 대비 이 비율보다 적으면, VLM bbox 자체가 실제 띠 범위를
# 잘못 좁게 줬을 가능성이 높다고 보고 안전하게 제한 없는(유물 전체 폭) 결과로
# 되돌아간다.


@dataclass(frozen=True)
class PatternMaskResult:
    mask: np.ndarray
    sam_score: float | None
    artifact_area_ratio: float
    image_area_ratio: float
    method: str
    # border_band 전용. 화면에 보이는 mask는 "실제 잉크가 있는 픽셀만" 남긴
    # 결과라 스캘럽/물결 사이 빈 유약 부분은 빠져 있다. 문제는 이 빈 부분이
    # "띠가 없는 곳"이 아니라 "잉크가 옅게/끊겨서 안 잡힌, 그래도 여전히
    # 띠 영역인 곳"일 수 있다는 것 - large_continuous(용문 등)가 나중에
    # exclusion_mask로 쓸 때 mask(잉크만)를 쓰면 이 틈을 "아무도 안 가진
    # 땅"으로 오인해서 자기 bbox 안에 있으면 그대로 삼켜버린다(용 몸통과
    # 안 이어진 채 독립된 네모난 조각이 떠 보이는 형태로 관찰됨). 그래서
    # 잉크 필터링 전의 "띠 세로 범위 전체(가로는 유물 폭 그대로)" 사각
    # 지대를 exclusion_zone에 별도로 담아, 화면 표시(mask)와 다른 문양
    # 침범 방지(exclusion_zone) 목적을 분리했다. large_continuous가 아닌
    # scope에서는 None이며, 그 경우 호출부는 mask를 그대로 exclusion에 쓰면 된다.
    exclusion_zone: np.ndarray | None = None
    # large_continuous 전용. 0~1 사이 값의 실수 배열로, roi(안전한 경계) 밖은
    # 항상 0이 되도록 클리핑되어 있다. 렌더링에서만 쓰이고, 마스크 자체나
    # exclusion_mask 계산에는 영향을 주지 않는다 (그러니 안전성 보장은 그대로 유지됨).
    density_map: np.ndarray | None = None


def load_font(size: int = 18) -> ImageFont.ImageFont:
    for path in [
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/malgun.ttf",
    ]:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def expand_artifact_mask(artifact_mask: np.ndarray, kernel_size: int = 15) -> np.ndarray:
    binary = np.asarray(artifact_mask, dtype=bool)
    kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
    return cv2.dilate(binary.astype(np.uint8), kernel, iterations=1).astype(bool)


def artifact_bbox(artifact_mask: np.ndarray) -> tuple[int, int, int, int]:
    mask = np.asarray(artifact_mask, dtype=bool)
    ys, xs = np.where(mask)
    if len(xs) == 0:
        raise RuntimeError("유물 마스크 면적이 0입니다.")
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def mask_to_contour_points(mask: np.ndarray, min_area: int = 20) -> list[list[tuple[int, int]]]:
    """마스크의 외곽선을 다각형 점 목록으로 뽑는다.

    실사용 테스트에서 문양 영역을 진한 반투명 색으로 꽉 채우면 원본
    문양 자체가 잘 안 보이는(육안조사 목적에 안 맞는) 문제가 있었다.
    채우기는 아주 옅게만 하고, 대신 경계선을 또렷하게 그려서 원본
    문양은 그대로 보이면서 영역 경계만 명확히 표시되게 한다.
    """
    binary = (np.asarray(mask, dtype=bool).astype(np.uint8)) * 255
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    polygons: list[list[tuple[int, int]]] = []
    for contour in contours:
        if cv2.contourArea(contour) < min_area:
            continue
        points = [(int(point[0][0]), int(point[0][1])) for point in contour]
        if len(points) >= 3:
            polygons.append(points)
    return polygons


def mask_bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    binary = np.asarray(mask, dtype=bool)
    ys, xs = np.where(binary)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def snap_point_to_artifact(
    artifact_mask: np.ndarray,
    x: int,
    y: int,
    max_distance_ratio: float = 0.05,
) -> tuple[int, int, float] | None:
    mask = np.asarray(artifact_mask, dtype=bool)
    height, width = mask.shape
    if 0 <= x < width and 0 <= y < height and mask[y, x]:
        return x, y, 0.0

    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None

    squared = (xs - x) ** 2 + (ys - y) ** 2
    index = int(np.argmin(squared))
    distance = float(np.sqrt(squared[index]))
    max_distance = float(np.hypot(width, height) * max_distance_ratio)
    if distance > max_distance:
        return None
    return int(xs[index]), int(ys[index]), distance


def percent_bbox_to_pixels(
    pattern: PatternLocation,
    width: int,
    height: int,
) -> np.ndarray | None:
    values = (
        pattern.bbox_x1_percent,
        pattern.bbox_y1_percent,
        pattern.bbox_x2_percent,
        pattern.bbox_y2_percent,
    )
    if any(value is None for value in values):
        return None

    x1 = int(float(pattern.bbox_x1_percent) / 100 * width)
    y1 = int(float(pattern.bbox_y1_percent) / 100 * height)
    x2 = int(float(pattern.bbox_x2_percent) / 100 * width)
    y2 = int(float(pattern.bbox_y2_percent) / 100 * height)

    x1 = max(0, min(width - 1, x1))
    y1 = max(0, min(height - 1, y1))
    x2 = max(x1 + 1, min(width, x2))
    y2 = max(y1 + 1, min(height, y2))
    return np.array([x1, y1, x2, y2], dtype=np.float32)


def clip_box_to_artifact(
    box: np.ndarray | list[int] | tuple[int, int, int, int],
    artifact_mask: np.ndarray,
) -> list[int]:
    ax1, ay1, ax2, ay2 = artifact_bbox(artifact_mask)
    x1, y1, x2, y2 = [int(value) for value in box]
    x1 = max(ax1, x1)
    y1 = max(ay1, y1)
    x2 = min(ax2, x2)
    y2 = min(ay2, y2)
    if x2 <= x1 or y2 <= y1:
        return [ax1, ay1, ax2, ay2]
    return [x1, y1, x2, y2]


def bbox_region_mask(
    pattern: PatternLocation,
    width: int,
    height: int,
    margin_ratio: float = 0.015,
) -> np.ndarray | None:
    box = percent_bbox_to_pixels(pattern, width, height)
    if box is None:
        return None
    x1, y1, x2, y2 = [int(value) for value in box]
    margin_x = int(width * margin_ratio)
    margin_y = int(height * margin_ratio)
    x1 = max(0, x1 - margin_x)
    y1 = max(0, y1 - margin_y)
    x2 = min(width, x2 + margin_x)
    y2 = min(height, y2 + margin_y)
    region = np.zeros((height, width), dtype=bool)
    region[y1:y2, x1:x2] = True
    return region


def fallback_bbox(
    scope: str,
    x: int,
    y: int,
    width: int,
    height: int,
) -> np.ndarray:
    if scope == "large_continuous":
        half_width, half_height = int(width * 0.24), int(height * 0.20)
    else:
        half_width, half_height = int(width * 0.07), int(height * 0.07)

    return np.array(
        [
            max(0, x - half_width),
            max(0, y - half_height),
            min(width, x + half_width),
            min(height, y + half_height),
        ],
        dtype=np.float32,
    )


def compute_ink_mask(
    image_rgb: np.ndarray,
    artifact_mask: np.ndarray,
    min_distance: float = MIN_BACKGROUND_DISTANCE,
) -> np.ndarray:
    """유물 표면에서 배경(유약)색과 대비되는 안료(문양) 픽셀을 찾는다.

    SAM2 point/box 프롬프트는 뚜렷한 경계를 가진 '덩어리'를 세그멘테이션
    하도록 설계되어 있는데, 청화백자 같은 얇은 선묘 문양은 흰 배경과
    선 사이에 그런 덩어리가 없다. 대신 유물 표면색의 분포에서 배경(유약)
    색을 중앙값으로 추정하고, Otsu 이진화로 그 색과 충분히 먼 픽셀만
    '문양 잉크'로 분리한다. 색상 대비가 뚜렷한 청화/철화/진사 등에는
    잘 맞지만, 문양과 배경 색이 거의 같은 백자 양각/음각 문양 등에는
    잘 맞지 않을 수 있다 (그런 경우는 여전히 근사 표시로 폴백된다).
    """
    mask = np.asarray(artifact_mask, dtype=bool)
    height, width = mask.shape
    if mask.sum() < 100:
        return np.zeros((height, width), dtype=bool)

    lab = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    surface_pixels = lab[mask]
    background_color = np.median(surface_pixels, axis=0)
    distance = np.linalg.norm(lab - background_color, axis=2)

    surface_distance = distance[mask]
    if surface_distance.size == 0:
        return np.zeros((height, width), dtype=bool)

    scaled = np.clip(surface_distance, 0, 255).astype(np.uint8).reshape(-1, 1)
    otsu_value, _ = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    threshold = max(float(otsu_value), min_distance)

    ink_mask = np.logical_and(distance >= threshold, mask)

    # 주의: 여기서 MORPH_OPEN(침식 후 팽창)을 쓰면 사진 해상도에 따라
    # 2~3px 두께밖에 안 되는 가는 붓선이 침식 단계에서 통째로 사라진다
    # (3x3 침식은 두께가 3px 미만인 영역을 완전히 지운다). 실제 테스트에서
    # 이 문제로 대형 연속 문양(용문 등)의 얇은 선묘가 전부 사라지는 걸
    # 확인해서 침식은 빼고, 안티앨리어싱으로 끊긴 픽셀만 살짝 이어붙이는
    # 팽창만 최소한으로 적용한다.
    kernel = np.ones((2, 2), dtype=np.uint8)
    cleaned = cv2.dilate(ink_mask.astype(np.uint8), kernel, iterations=1)
    return cleaned.astype(bool)


def build_large_continuous_mask(
    pattern: PatternLocation,
    artifact_mask: np.ndarray,
    ink_mask: np.ndarray,
    image_width: int,
    image_height: int,
    exclusion_mask: np.ndarray | None = None,
) -> PatternMaskResult:
    """대형 연속 문양(용/봉황 등)의 영역을 VLM bbox 기준으로 표시한다.

    지금까지 세 번 고쳐봤다: (1) bbox 안 잉크를 크게 팽창시켜 뭉치기 ->
    근처의 무관한 구름까지 삼킴. (2) 팽창을 줄이고 "가장 큰 연결요소"만
    남기기 -> bbox가 띠와 몇 %p만 떨어져 있으면 맞닿아서 띠 전체(하나의
    큰 연결 구조)까지 통째로 흡수해버림. (3) 다른 문양이 이미 확정한
    영역을 잉크 탐색 전에 제외하기 -> 실패한 다른 문양의 대략적 bbox까지
    제외 목록에 들어가면서 정상적인 용 영역에 각진 홈이 파임.

    세 번 다 "잉크를 따라 정밀하게 윤곽을 그리려는 시도"가 원인이었다.
    실제 요구사항은 픽셀 단위로 완벽한 마스킹이 아니라 "이 부분이 용
    영역이라는 걸 알 수 있으면 되고, 그 대신 다른 문양 영역은 절대
    침범하면 안 된다"는 것이었다. 그래서 잉크/연결요소 분석을 아예
    빼고, VLM이 준 bbox를 유물 실루엣과 교집합한 뒤, 이미 확정된 다른
    문양(exclusion_mask)을 명시적으로 빼는 것만으로 영역을 정의한다.
    다소 각지고 넉넉해 보일 수 있지만, "다른 문양을 침범하지 않는다"는
    조건을 집합 연산(빼기)으로 항상 보장할 수 있어 훨씬 안정적이다.
    """
    artifact = np.asarray(artifact_mask, dtype=bool)
    artifact_area = int(artifact.sum())
    if artifact_area == 0:
        raise RuntimeError("유물 마스크 면적이 0입니다.")

    x = int(pattern.x_percent / 100 * image_width)
    y = int(pattern.y_percent / 100 * image_height)

    # 마진을 따로 더하지 않는다 - VLM bbox 자체가 이미 넉넉한 편이라,
    # 마진을 더할수록 이웃 문양 영역과 맞닿을 위험만 커진다.
    roi = bbox_region_mask(pattern, image_width, image_height, margin_ratio=0.0)
    if roi is None:
        box = fallback_bbox(pattern.pattern_scope, x, y, image_width, image_height)
        x1, y1, x2, y2 = [int(value) for value in box]
        roi = np.zeros_like(artifact)
        roi[y1:y2, x1:x2] = True
    roi = np.logical_and(roi, artifact)

    if exclusion_mask is not None:
        roi = np.logical_and(roi, np.logical_not(np.asarray(exclusion_mask, dtype=bool)))

    area = int(roi.sum())
    if area == 0:
        raise RuntimeError(
            "large_continuous 영역이 유물 밖이거나 다른 문양에 전부 배정되어 있습니다."
        )

    # roi 자체(경계/침범 방지 기준)는 그대로 두되, 화면에 칠할 때만 쓸
    # 소프트 알파(밀도) 맵을 만든다. roi 안의 실제 잉크 픽셀을 블러해서
    # "잉크가 몰린 곳=진하게, 빈 유약 부분=옅게" 그라데이션을 만들고,
    # 마지막에 다시 roi로 곱해 경계 밖으로는 절대 안 새게 한다 (블러 자체는
    # 경계 밖으로 퍼질 수 있지만, roi 곱셈으로 확실히 잘라낸다). 그 결과
    # 화면에서는 마치 용의 선묘를 따라가는 것처럼 진하게/연하게 보이면서도,
    # 실제 경계(roi)는 여전히 다른 문양을 침범하지 않는 안전한 사각형이다.
    region_ink = np.logical_and(ink_mask, roi)
    density_map: np.ndarray | None = None
    if region_ink.any():
        blurred = cv2.GaussianBlur(
            region_ink.astype(np.float32), (0, 0), sigmaX=DENSITY_BLUR_SIGMA
        )
        blurred = blurred * roi.astype(np.float32)  # roi 밖은 확실히 0으로
        peak = float(blurred.max())
        if peak > 0:
            # 완전히 0으로 떨어지지 않게 바닥값을 살짝 깔아둔다 - 안 그러면
            # 잉크에서 조금만 멀어져도 아예 안 보여서 오히려 "구멍 난 것"
            # 처럼 보일 수 있다.
            density_map = np.clip(blurred / peak, 0.0, 1.0)
            density_map = np.where(roi, np.maximum(density_map, 0.12), 0.0)

    return PatternMaskResult(
        mask=roi,
        sam_score=None,
        artifact_area_ratio=float(area / artifact_area),
        image_area_ratio=float(area / roi.size),
        method="bbox_minus_claimed",
        density_map=density_map,
    )


def build_border_band_mask(
    pattern: PatternLocation,
    artifact_mask: np.ndarray,
    ink_mask: np.ndarray,
) -> PatternMaskResult:
    """띠 문양 영역을, 세로 범위는 잉크 밀도 프로파일로 찾고 가로로는
    실제 잉크 픽셀만 남기는 방식으로 찾는다.

    이전 버전은 세로 범위(상단/하단 몇 %인지)만 잉크 밀도로 찾고, 그 구간
    안에서는 유물 폭 전체를 그대로 채웠다. 그 결과 스캘럽/물결/연판 문양
    사이사이 빈 배경까지 포함해서 꼭짓점이 각진 네모난 박스처럼 보이는
    문제가 있었다 (같은 화면에서 용문은 실제 선묘를 따라가는 윤곽선으로
    보이는 것과 대비되어 더 어색해 보였다). 이번 버전은 세로 범위를 찾은
    다음, 그 구간 안에서도 실제 잉크가 있는 픽셀만 남겨 스캘럽 하나하나의
    모양을 따라가는 윤곽선이 나오게 한다.
    """
    artifact = np.asarray(artifact_mask, dtype=bool)
    height, width = artifact.shape
    x1, y1, x2, y2 = artifact_bbox(artifact)
    artifact_height = max(1, y2 - y1)

    image_center_y = float(pattern.y_percent) / 100 * height
    relative_y = (image_center_y - y1) / artifact_height

    if relative_y <= 0.33:
        search_start, search_end = 0.0, 0.35
        method = "ink_top_band"
    elif relative_y >= 0.68:
        search_start, search_end = 0.65, 1.0
        method = "ink_bottom_band"
    else:
        search_start = max(0.0, relative_y - 0.15)
        search_end = min(1.0, relative_y + 0.15)
        method = "ink_middle_band"

    row_start = y1 + int(artifact_height * search_start)
    row_end = y1 + int(artifact_height * search_end)
    row_end = max(row_start + 1, row_end)
    row_end = min(row_end, height)

    row_width = artifact[row_start:row_end, :].sum(axis=1)
    row_ink = np.logical_and(ink_mask, artifact)[row_start:row_end, :].sum(axis=1)
    row_ratio = np.divide(
        row_ink,
        row_width,
        out=np.zeros_like(row_ink, dtype=np.float64),
        where=row_width > 0,
    )

    # 노이즈로 인한 단일 행 오탐을 줄이기 위한 이동평균 스무딩.
    smooth_window = max(3, int(artifact_height * 0.01) | 1)  # 홀수로 보정
    kernel = np.ones(smooth_window) / smooth_window
    smoothed_ratio = np.convolve(row_ratio, kernel, mode="same") if len(row_ratio) else row_ratio

    # median*factor 방식은 탐색 구간 안의 잉크가 사실상 띠 하나뿐인 경우
    # (즉 대부분 행이 0) median 자체가 이미 띠의 값이 되어 버려서 threshold가
    # 최댓값(1.0 근처)을 넘어가 절대 못 넘는 문제가 있었다. 대신 행별 밀도
    # 분포에 1차원 Otsu 이진화를 적용해 '배경 행'과 '띠 행'을 자동으로
    # 나눈다. 잉크가 아예 없으면(모두 0) dense_rows는 빈 배열로 남는다.
    if smoothed_ratio.size and smoothed_ratio.max() > 0:
        scaled_rows = np.clip(
            smoothed_ratio / smoothed_ratio.max() * 255, 0, 255
        ).astype(np.uint8).reshape(-1, 1)
        otsu_val, _ = cv2.threshold(scaled_rows, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        dense_threshold = max(
            BAND_DENSE_ROW_FLOOR, (otsu_val / 255.0) * smoothed_ratio.max()
        )
        dense_rows = np.where(smoothed_ratio >= dense_threshold)[0]
    else:
        dense_rows = np.array([], dtype=int)

    if len(dense_rows) == 0:
        # 밀도 기반 탐지 실패 -> 탐색 구간 전체를 그대로 사용 (예전 방식으로 폴백)
        band_y1, band_y2 = row_start, row_end
        method += "_range_fallback"
    else:
        pad = max(2, int(artifact_height * BAND_ROW_PADDING_RATIO))
        band_y1 = max(row_start, row_start + int(dense_rows.min()) - pad)
        band_y2 = min(row_end, row_start + int(dense_rows.max()) + 1 + pad)

    band_zone = np.zeros_like(artifact)
    band_zone[band_y1:band_y2, :] = True
    band_region = np.logical_and(artifact, band_zone)

    # 가로 범위를 VLM이 준 bbox_percent로 좁힌다 (있을 때만). bbox 폭에
    # 10% 여유를 더하고, 그래도 유물 폭의 BAND_MIN_WIDTH_RATIO보다 좁으면
    # 중심을 유지한 채 그만큼까지 대칭 확장한다. bbox가 없으면 예전처럼
    # 유물 폭 전체를 그대로 쓴다.
    band_region_restricted = band_region
    horizontal_roi_applied = False
    ax1, ay1, ax2, ay2 = x1, y1, x2, y2  # artifact_bbox 결과(위에서 이미 계산됨)
    if pattern.bbox_x1_percent is not None and pattern.bbox_x2_percent is not None:
        bbox_x1_px = float(pattern.bbox_x1_percent) / 100 * width
        bbox_x2_px = float(pattern.bbox_x2_percent) / 100 * width
        if bbox_x2_px < bbox_x1_px:
            bbox_x1_px, bbox_x2_px = bbox_x2_px, bbox_x1_px
        bbox_w = max(1.0, bbox_x2_px - bbox_x1_px)
        pad = bbox_w * BAND_HORIZONTAL_PAD_RATIO
        roi_x1 = bbox_x1_px - pad
        roi_x2 = bbox_x2_px + pad

        min_width = max(1.0, ax2 - ax1) * BAND_MIN_WIDTH_RATIO
        if (roi_x2 - roi_x1) < min_width:
            center = (roi_x1 + roi_x2) / 2
            roi_x1 = center - min_width / 2
            roi_x2 = center + min_width / 2

        roi_x1 = max(float(ax1), roi_x1)
        roi_x2 = min(float(ax2), roi_x2)

        if roi_x2 > roi_x1:
            horizontal_roi = np.zeros_like(artifact)
            horizontal_roi[:, int(roi_x1):int(roi_x2)] = True
            band_region_restricted = np.logical_and(band_region, horizontal_roi)
            horizontal_roi_applied = True

    # 제한한 결과의 잉크 픽셀이 제한 전보다 지나치게 적으면(=VLM bbox가
    # 실제 띠 범위를 잘못 좁게 줬을 가능성), 안전하게 제한 없는 결과로
    # 되돌아간다 - bbox를 그대로 신뢰해 잘라내지 않는다.
    band_ink_full = np.logical_and(ink_mask, band_region)
    band_ink_restricted = (
        np.logical_and(ink_mask, band_region_restricted)
        if horizontal_roi_applied
        else band_ink_full
    )
    full_ink_count = int(band_ink_full.sum())
    restricted_ink_count = int(band_ink_restricted.sum())
    if (
        horizontal_roi_applied
        and full_ink_count > 0
        and restricted_ink_count < full_ink_count * BAND_RESTRICTED_MIN_RATIO
    ):
        band_region_final = band_region
        method += "_bbox_roi_fallback"
    elif horizontal_roi_applied:
        band_region_final = band_region_restricted
        method += "_bbox_roi"
    else:
        band_region_final = band_region

    # 세로(및 이제 가로) 범위 안에서도 실제 잉크가 있는 곳만 남긴다 (통째로
    # 채우지 않는다). 붓선/스캘럽 사이의 미세한 끊김만 최소한으로 이어붙인다.
    band_ink = np.logical_and(ink_mask, band_region_final)
    if band_ink.any():
        bridge_kernel = np.ones((INK_BRIDGE_KERNEL, INK_BRIDGE_KERNEL), dtype=np.uint8)
        band_mask = cv2.dilate(band_ink.astype(np.uint8), bridge_kernel, iterations=1).astype(bool)
        band_mask = np.logical_and(band_mask, band_region_final)
    else:
        # 잉크를 하나도 못 찾았으면(색 대비가 약함 등) 예전처럼 구간 전체를 근사치로 사용
        band_mask = band_region_final
        method += "_ink_fallback"

    area = int(band_mask.sum())
    artifact_area = int(artifact.sum())
    if area == 0 or artifact_area == 0:
        raise RuntimeError("띠 영역을 만들지 못했습니다.")

    return PatternMaskResult(
        mask=band_mask,
        sam_score=None,
        artifact_area_ratio=float(area / artifact_area),
        image_area_ratio=float(area / band_mask.size),
        method=method,
        # 잉크 유무와 무관하게 "이 구간은 띠 영역이다"라고 확정된
        # band_region_final(가로 ROI 적용 후) 전체를 넘긴다. mask보다 넓을
        # 수 있는데, 그 여분이 바로 large_continuous가 침범하면 안 되는
        # 부분이다. 가로 ROI로 좁혔으므로, 그 바깥(예: 용 머리 영역)은 더
        # 이상 띠의 배타 영역으로 잘못 예약되지 않는다.
        exclusion_zone=band_region_final,
    )


def build_small_instance_mask(
    pattern: PatternLocation,
    artifact_mask: np.ndarray,
    ink_mask: np.ndarray,
    image_width: int,
    image_height: int,
    x: int,
    y: int,
    window_ratio: float = SMALL_INSTANCE_WINDOW_RATIO,
) -> PatternMaskResult:
    """개별 소형 문양(꽃/구름 한 개)을, 점 주변 잉크의 연결요소로 찾는다.

    이전 버전은 SAM2 point prompt를 썼는데, 실사용 테스트에서 대부분
    "유물 대비 면적 99%대"처럼 명백히 잘못된 마스크를 반환해 허용 범위
    (0.02%~6%)를 못 맞추고 근사 박스로 폴백되는 게 확인됐다(SAM2가 점
    하나만으로는 작은 선묘 문양의 경계를 제대로 못 잡음). 대신 점 주변
    국소 창 안에서, 점이 속하거나 점에서 가장 가까운 잉크 연결요소를
    그대로 문양 경계로 쓴다.
    """
    artifact = np.asarray(artifact_mask, dtype=bool)
    artifact_area = int(artifact.sum())
    if artifact_area == 0:
        raise RuntimeError("유물 마스크 면적이 0입니다.")

    half_w = int(image_width * window_ratio)
    half_h = int(image_height * window_ratio)
    wx1, wx2 = max(0, x - half_w), min(image_width, x + half_w)
    wy1, wy2 = max(0, y - half_h), min(image_height, y + half_h)

    window = np.zeros_like(artifact)
    window[wy1:wy2, wx1:wx2] = True
    local_ink = np.logical_and(ink_mask, np.logical_and(window, artifact))

    if not local_ink.any():
        raise RuntimeError("점 주변에서 잉크 픽셀을 찾지 못했습니다.")

    num_labels, labels = cv2.connectedComponents(local_ink.astype(np.uint8))
    if num_labels <= 1:
        raise RuntimeError("점 주변에서 잉크 연결요소를 찾지 못했습니다.")

    point_label = int(labels[y, x]) if (0 <= y < image_height and 0 <= x < image_width and artifact[y, x]) else 0
    if point_label == 0:
        ys, xs = np.where(labels > 0)
        if len(xs) == 0:
            raise RuntimeError("점 주변에서 잉크 연결요소를 찾지 못했습니다.")
        distances = (xs - x) ** 2 + (ys - y) ** 2
        nearest = int(np.argmin(distances))
        point_label = int(labels[ys[nearest], xs[nearest]])

    component = labels == point_label

    # 붓 터치 사이 미세한 공백 정도만 이어붙인다.
    kernel = np.ones((5, 5), dtype=np.uint8)
    grown = cv2.dilate(component.astype(np.uint8), kernel, iterations=1).astype(bool)
    grown = np.logical_and(grown, np.logical_and(window, artifact))

    area = int(grown.sum())
    if area == 0:
        raise RuntimeError("소형 문양 영역을 찾지 못했습니다.")

    artifact_area_ratio = float(area / artifact_area)
    if artifact_area_ratio > SMALL_INSTANCE_MAX_ARTIFACT_RATIO:
        # 장식이 빽빽한 구역에서는 점 주변 연결요소가 실제로는 여러 문양이
        # 서로 이어진 큰 덩어리일 수 있다. "작은 개별 문양"이라기엔 너무
        # 크므로 실패시켜 근사 박스로 폴백하게 한다 (SAM2 시절의
        # SCOPE_RATIO_LIMITS 상한선과 같은 역할).
        raise RuntimeError(
            "점 주변 연결요소가 너무 큼 "
            f"(유물 대비 면적 {artifact_area_ratio:.1%}, "
            f"허용 상한 {SMALL_INSTANCE_MAX_ARTIFACT_RATIO:.1%}) - "
            "주변 장식과 붙어 있어 개별 문양으로 분리되지 않은 것으로 보임"
        )

    window_artifact_area = int(np.logical_and(window, artifact).sum())
    if window_artifact_area > 0:
        window_fill_ratio = area / window_artifact_area
        if window_fill_ratio > SMALL_INSTANCE_MAX_WINDOW_FILL_RATIO:
            # 유물 전체 대비로는 작아도(위 검사 통과), 국소 창 자체를 거의
            # 다 채웠다면 실제 경계가 창 밖으로 계속 이어지는 큰 덩어리를
            # 창이 잘라낸 것일 가능성이 높다 - 그 결과가 각진 사각형(창
            # 경계 그대로)으로 화면에 나타나는 문제가 실사용 테스트에서
            # 확인됐다(상단 띠 근처 구름 점이 띠 잉크와 이어져 있던 경우).
            raise RuntimeError(
                "점 주변 연결요소가 국소 창을 거의 다 채움 "
                f"(창 대비 면적 {window_fill_ratio:.1%}, "
                f"허용 상한 {SMALL_INSTANCE_MAX_WINDOW_FILL_RATIO:.1%}) - "
                "창 경계에서 잘린 큰 덩어리일 가능성이 높아 개별 문양으로 보지 않음"
            )

    return PatternMaskResult(
        mask=grown,
        sam_score=None,
        artifact_area_ratio=artifact_area_ratio,
        image_area_ratio=float(area / grown.size),
        method="ink_connected_component",
    )


def get_pattern_mask(
    pattern: PatternLocation,
    artifact_mask: np.ndarray,
    image_width: int,
    image_height: int,
    x: int,
    y: int,
    ink_mask: np.ndarray,
    exclusion_mask: np.ndarray | None = None,
) -> PatternMaskResult:
    artifact = np.asarray(artifact_mask, dtype=bool)
    artifact_area = int(artifact.sum())
    if artifact_area == 0:
        raise RuntimeError("유물 마스크 면적이 0입니다.")

    if pattern.pattern_scope == "border_band":
        return build_border_band_mask(pattern, artifact, ink_mask)

    if pattern.pattern_scope == "large_continuous":
        return build_large_continuous_mask(
            pattern, artifact, ink_mask, image_width, image_height, exclusion_mask=exclusion_mask
        )

    return build_small_instance_mask(pattern, artifact, ink_mask, image_width, image_height, x, y)


# 과거 코드와의 최소 호환용 (SAM2 point 직접 호출이 필요한 다른 코드가
# 있을 경우에 대비해 남겨둔다. get_pattern_mask는 더 이상 이걸 쓰지 않는다.)
def get_point_mask(detector: GroundedSAMDetector, x: int, y: int) -> np.ndarray:
    masks, scores, _ = detector.sam2_predictor.predict(
        point_coords=np.array([[x, y]], dtype=np.float32),
        point_labels=np.array([1], dtype=np.int32),
        multimask_output=True,
    )
    if masks is None or len(masks) == 0:
        raise RuntimeError("SAM2가 마스크를 생성하지 못했습니다.")
    valid = [
        (np.asarray(mask).squeeze().astype(bool), float(score))
        for mask, score in zip(masks, scores)
    ]
    return max(valid, key=lambda item: item[1])[0]


def main(image_path: str, out_path: str, n_calls: int = 3) -> None:
    analysis = analyze_pottery_patterns_ensemble(image_path, n_calls=n_calls)
    detector = GroundedSAMDetector(text_prompt=POTTERY_TEXT_PROMPT, max_regions=1)
    regions = detector.predict(image_path=image_path)
    if not regions:
        raise RuntimeError("도자기 유물 영역을 찾지 못했습니다.")

    artifact_mask = np.asarray(regions[0]["mask"], dtype=bool)
    image = Image.open(image_path).convert("RGBA")
    width, height = image.size
    image_rgb = np.array(image.convert("RGB"))
    ink_mask = compute_ink_mask(image_rgb, artifact_mask)

    mask_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    annotation_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(annotation_layer)
    font = load_font(18)

    names = sorted({pattern.pattern_name for pattern in analysis.patterns})
    color_map = {
        name: BADGE_COLORS[index % len(BADGE_COLORS)]
        for index, name in enumerate(names)
    }

    for pattern in analysis.patterns:
        x = int(pattern.x_percent / 100 * width)
        y = int(pattern.y_percent / 100 * height)
        if pattern.pattern_scope != "border_band":
            snapped = snap_point_to_artifact(artifact_mask, x, y)
            if snapped is None:
                print(f"[제외] {pattern.pattern_name}: 유물에서 너무 먼 좌표")
                continue
            x, y, _ = snapped
        color = color_map[pattern.pattern_name]

        try:
            mask_result = get_pattern_mask(
                pattern,
                artifact_mask,
                width,
                height,
                x,
                y,
                ink_mask=ink_mask,
            )
            box = mask_bbox(mask_result.mask)
            if box is not None:
                x = (box[0] + box[2]) // 2
                y = (box[1] + box[3]) // 2
            if mask_result.density_map is not None:
                density_array = np.clip(mask_result.density_map, 0.0, 1.0)
                mask_image = Image.fromarray((density_array * 255).astype(np.uint8), mode="L")
            else:
                mask_image = Image.fromarray((mask_result.mask * 255).astype(np.uint8), mode="L")
            # 채우기는 아주 옅게만 한다 (실사용 테스트에서 진하게 칠하면
            # 원본 문양 자체가 안 보이는 문제가 있었음). 경계는 아래에서
            # 윤곽선으로 또렷하게 그린다.
            alpha = 30 if pattern.pattern_scope != "small_instance" else 90
            colored = Image.new("RGBA", image.size, color + (alpha,))
            transparent = Image.new("RGBA", image.size, (0, 0, 0, 0))
            mask_layer = Image.alpha_composite(
                mask_layer,
                Image.composite(colored, transparent, mask_image),
            )
            for polygon in mask_to_contour_points(mask_result.mask):
                draw.line(polygon + [polygon[0]], fill=color + (255,), width=2)
        except Exception as error:
            print(f"[근사 표시] {pattern.pattern_name}: {error}")
            box = percent_bbox_to_pixels(pattern, width, height)
            if box is not None:
                draw.rectangle(
                    tuple(clip_box_to_artifact(box, artifact_mask)),
                    outline=color + (255,),
                    width=4,
                )
            else:
                draw.ellipse([x - 20, y - 20, x + 20, y + 20], outline=color + (255,), width=4)

        draw.ellipse([x - 5, y - 5, x + 5, y + 5], fill=color + (255,), outline=(0, 0, 0, 255))
        draw.text((x + 8, y - 10), pattern.pattern_name, fill=color + (255,), font=font)

    combined = Image.alpha_composite(image, mask_layer)
    combined = Image.alpha_composite(combined, annotation_layer)
    combined.convert("RGB").save(out_path)
    print(f"결과 저장: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--out", default="precise_pattern_result.png")
    parser.add_argument("--n-calls", type=int, default=3)
    arguments = parser.parse_args()
    main(arguments.image, arguments.out, arguments.n_calls)