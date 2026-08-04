"""
glaze_detector.py

도자기 표면에 유약(광택)이 있는지 없는지를 판별하는 시작 코드.

접근 방식
---------
유약을 바른 도자기 표면은 빛을 정반사(specular reflection)해서
좁고 밝은 하이라이트가 생긴다. 무유(유약 없음) 표면은 난반사가
많아 밝기가 완만하게 퍼진다. 그래서:

1. 밝기(V 채널) 중 상위 퍼센타일 이상인 픽셀 비율 (하이라이트 면적)
2. 그 하이라이트 영역의 "뾰족함" (밝기 분포의 첨도, kurtosis) -
   좁고 강한 하이라이트일수록 첨도가 높다
3. 채도(S 채널) 표준편차 - 유광 표면은 하이라이트 부분에서
   채도가 급격히 낮아지는 경향이 있다 (하이라이트는 흰색에 가까움)

를 특징으로 뽑아서 규칙 기반으로 1차 분류한다.

주의 - 이건 최종 분류기가 아니라 시작점이다
-------------------------------------------
아직 라벨링된 도자기 사진이 없어서, 아래 THRESHOLD 값들은
전부 임시값(placeholder)이다. 실제 유광/무유 도자기 사진을
10~20장씩만 모아도 이 임계값을 실측 분포로 바로 교체해야 한다.
사진이 쌓이면 이 특징들(highlight_area_ratio, highlight_kurtosis,
saturation_std)을 그대로 Random Forest 입력으로 써도 된다 -
부식 프로젝트에서 했던 "규칙 기반 후보 생성 -> 특징 추출 ->
소규모 분류기 학습" 흐름과 동일한 패턴이다.

사용법:
    from glaze_detector import estimate_glaze
    result = estimate_glaze(image_bgr, object_mask)
"""

from dataclasses import dataclass

import cv2
import numpy as np
from scipy.stats import kurtosis

# 잠정 임계값 - 실제 도자기 사진으로 반드시 재보정할 것
THRESHOLDS = {
    "highlight_percentile": 95,  # 밝기 상위 5%를 하이라이트로 간주
    "highlight_area_ratio_min": 0.005,  # 이 이상이면 하이라이트 있음 후보
    "highlight_kurtosis_min": 3.0,  # 정규분포 첨도(3) 이상이면 뾰족한 분포
    "saturation_std_min": 15.0,  # 채도 변화가 크면 유광 가능성 ↑
}


@dataclass
class GlazeEstimate:
    has_glaze_guess: bool
    confidence_label: str  # "낮음" | "중간" | "높음" - 아직 통계적 신뢰도 아님, 정성적 표현
    highlight_area_ratio: float
    highlight_kurtosis: float
    saturation_std: float
    note: str


def estimate_glaze(image_bgr: np.ndarray, object_mask: np.ndarray) -> GlazeEstimate:
    """object_mask: 도자기 영역만 True인 bool 배열 (SAM2 등으로 미리 분리)."""

    binary_mask = np.asarray(object_mask, dtype=bool)
    if binary_mask.sum() == 0:
        raise ValueError("object_mask에 유효한 픽셀이 없습니다.")

    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    v_channel = hsv[..., 2][binary_mask].astype(np.float32)
    s_channel = hsv[..., 1][binary_mask].astype(np.float32)

    highlight_threshold = np.percentile(
        v_channel, THRESHOLDS["highlight_percentile"]
    )
    highlight_pixels = v_channel >= highlight_threshold
    highlight_area_ratio = float(highlight_pixels.sum()) / len(v_channel)

    # 첨도: 표본이 너무 적으면 (예: 마스크가 매우 작으면) nan이 나올 수 있어 방어
    v_kurtosis = float(kurtosis(v_channel)) if len(v_channel) > 20 else 0.0
    saturation_std = float(s_channel.std())

    votes = 0
    if highlight_area_ratio >= THRESHOLDS["highlight_area_ratio_min"]:
        votes += 1
    if v_kurtosis >= THRESHOLDS["highlight_kurtosis_min"]:
        votes += 1
    if saturation_std >= THRESHOLDS["saturation_std_min"]:
        votes += 1

    has_glaze_guess = votes >= 2
    confidence_label = {0: "낮음", 1: "낮음", 2: "중간", 3: "높음"}[votes]

    return GlazeEstimate(
        has_glaze_guess=has_glaze_guess,
        confidence_label=confidence_label,
        highlight_area_ratio=round(highlight_area_ratio, 4),
        highlight_kurtosis=round(v_kurtosis, 2),
        saturation_std=round(saturation_std, 2),
        note=(
            "규칙 기반 잠정 추정치입니다. THRESHOLDS는 실제 사진으로 "
            "재보정이 필요합니다 (현재 값은 플레이스홀더)."
        ),
    )
