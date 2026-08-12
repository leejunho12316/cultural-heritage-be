"""
pottery_analyzer.py

도자기 육안조사 통합 분석기.
- 완전/파편: 로컬 RF
- 표면 광택: 로컬 RF
- 시대: 로컬 CNN (멀티태스크 모델의 era 헤드만 사용)
- 문양 이름/위치: VLM ensemble
- 문양 마스크: 문양 범위에 따라 SAM2 point 또는 box prompt

개선 사항 (다른 LLM 리뷰 반영, 1차)
- border_band 문양의 이미지 배지가 실제 문양명을 무시하고 "상단띠1"/
  "하단띠1"처럼 위치 서술어로 강제 표시되던 버그를 고쳤다. 이건 애초에
  pottery_pattern_vlm_locator.py 프롬프트에서 고쳤다고 changelog에 적힌
  문제인데, 여기 배지 생성 로직이 별도로 위치명을 다시 덮어쓰고 있었다.
  이제 모든 scope에서 동일하게 실제 display_name 기반 배지를 쓴다.
- 배지 접두어를 1글자에서 2글자로 늘렸다. "연꽃문"/"연화문"처럼 첫 글자가
  같은 문양이 섞이면 1글자 배지로는 구분이 안 됐다.
- 이미지 우하단에 배지→실제 문양명 범례(legend)를 그려서, 배지 약어에
  의존하지 않고도 정확한 명칭을 바로 확인할 수 있게 했다. 참고 문양
  목록에 없는 명칭은 범례에 "[목록 외 명칭]"으로 표시해 검토를 유도한다.
- 기본 오버레이에 쓰던 "2회 이상 합의" 기준이 하드코딩되어 있어서
  n_calls를 1로 두면 항상 빈 화면이 나오는 문제가 있었다. 이제
  ensemble 결과가 돌려주는 min_agreement_used(실제 성공한 호출 수에
  맞춰 자동으로 낮아진 기준)를 사용한다.
- 시대 추정을 VLM이 아니라 예전에 학습해둔 로컬 CNN(멀티태스크 모델의
  era 헤드)으로 다시 살렸다. 교차검증 정확도가 문양(55~64%)/색상(33%)보다
  훨씬 나은 88%였던 부분이라, VLM으로 넘어가면서 같이 버릴 이유가 없었다.
  무료+로컬이라 문양(VLM, 유료)과 별개로 항상 같이 계산한다.

개선 사항 (2차, 실사용 피드백 3가지 + 추가 정리 반영)
- 기본 화면 표시 조건에 decision != "판정보류"를 추가했다. 기존에는
  agreement_count만 봤기 때문에, 여러 호출이 같은 낯선 이름을 반복해서
  냈지만 근거가 부족해 판정보류로 내려간 항목도 합의 횟수만 높으면 기본
  화면에 그대로 나오는 문제가 있었다. 판정보류 항목은 이제 상세 JSON에만
  남고 기본 화면/기본 이름 목록에서는 빠진다.
- border_band(띠 장식)는 SAFE_APPROXIMATE_MASKING_ONLY 안전 모드가
  켜져 있어도 항상 실제 build_border_band_mask()를 사용하도록 순서를
  바꿨다. 안전 모드가 필요했던 건 large_continuous의 예전 잉크 추적
  마스킹이었지, border_band는 애초에 문제가 된 적이 없었는데 같은
  플래그로 묶여서 근사 사각형으로 대체되고 있었다. border_band를 항상
  실제 마스킹으로 처리하면 exclusion_zone이 claimed_mask에 계속
  반영되어, 뒤에 처리되는 large_continuous(용 등)가 띠 영역을 침범하지
  않는다.
- _tighten_box_to_ink에 exclusion_mask(선택) 인자를 추가했다.
  large_continuous를 안전 모드로 타이트닝할 때 claimed_mask(주로
  border_band가 이미 차지한 구연부/굽 영역)를 넘겨줘서, 대형 문양의
  타이트닝된 박스가 이미 다른 문양이 차지한 자리로 파고들지 않게 한다.
- border_band 배지를 실제 문양명 대신 "상단띠"/"하단띠"처럼 위치 기준
  배지로 표시하도록 되돌렸다. 정밀 마스킹이 안전 근사 모드로 대체된
  상황에서는 "이 항목이 정확히 어떤 문양이다"보다 "구연부 쪽 띠 하나,
  굽 쪽 띠 하나가 있다"는 위치 구분이 조사자에게 더 실용적이라는 피드백을
  반영했다. 실제 문양 분류(display_name)는 JSON과 범례에는 그대로 노출된다.

개선 사항 (3차, 다중 객체 사진 대응)
- 발굴 현장에서 흔한 "여러 조각을 늘어놓고 찍은 사진"이 들어오면, 예전엔
  Grounding DINO + SAM2가 그중 하나(주로 화면에서 가장 도드라지는 영역)를
  임의로 골라 마치 그게 사진 전체를 대표하는 유물인 것처럼 완전/파편·유약·
  시대를 분석해버렸다. 이제 max_regions을 1보다 크게 잡아 여러 영역이
  감지되면, 그중 하나를 골라 분석하는 대신 분석을 중단하고 재촬영을
  안내한다. 참고용으로 색상 유사도 기반 그룹핑도 같이 제공하지만, 이건
  깨진 단면을 맞춰보는 진짜 재조립이 아니라 색·유약 톤만 비교한 거친
  힌트이므로 확정 근거로 쓰면 안 된다.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import cv2
import joblib
import numpy as np
import torch
from PIL import Image, ImageDraw

from models.grounded_sam_detector import GroundedSAMDetector
from completeness_classifier import (
    _largest_contour,
    _symmetry_iou,
    _circularity,
    _max_defect_depth_ratio,
)
from glaze_detector import estimate_glaze
from evaluate_glaze import imread_unicode_safe
from predict_pottery_multitask import (
    load_model as load_era_cnn_model,
    EVAL_TRANSFORM as ERA_EVAL_TRANSFORM,
)
from pottery_pattern_vlm_locator import (
    PatternLocation,
    analyze_pottery_patterns_ensemble,
    assess_pattern_conditions_in_parallel,
    explain_era_prediction,
)
from precise_pattern_masking import (
    BADGE_COLORS,
    build_border_band_mask,
    clip_box_to_artifact,
    compute_ink_mask,
    expand_artifact_mask,
    get_pattern_mask,
    load_font,
    mask_bbox,
    mask_to_contour_points,
    percent_bbox_to_pixels,
    snap_point_to_artifact,
)

POTTERY_TEXT_PROMPT = (
    "ceramic vessel. pottery. clay pot. "
    "ceramic fragment. broken pottery shard."
)
COMPLETENESS_FEATURES = [
    "symmetry_iou",
    "circularity",
    "max_defect_depth_ratio",
]
GLAZE_FEATURES = [
    "highlight_area_ratio",
    "highlight_kurtosis",
    "saturation_std",
]
DEFAULT_USE_VLM_PATTERN_ANALYSIS = True
FALLBACK_MIN_PATTERN_AGREEMENT = 2
SAFE_APPROXIMATE_MASKING_ONLY = True

# 클로즈업 육안상태조사(v9). 정밀 마스크/박스를 맞추는 대신, 이미 확정된
# (default_keys) 문양마다 원본에서 넉넉하게 크롭한 클로즈업 이미지를 VLM에
# 다시 보여주고 보존 상태(마모/박락/변색/균열/오염)를 평가한다. 비용을
# 고려해 확정 문양에 대해서만, 문양당 단일 호출로 수행한다.
ENABLE_CONDITION_REVIEW = True
# 크롭 박스는 "정확한 경계"가 아니라 "이 근처를 넉넉히 포함"하면 되므로
# 이미 있는 근사 박스(있으면)에 스코프별 여백만 더한다. border_band는
# 이미 폭이 넓어서 여백을 적게, small_instance는 원래 작아서 여백을
# 넉넉하게 줬다.
CONDITION_CROP_PADDING_RATIO = {
    "small_instance": 0.35,
    "border_band": 0.15,
    "large_continuous": 0.10,
}
CONDITION_CROP_MIN_SIDE_PX = 180
# bbox가 아예 없어 점(point) 정보만 있는 경우(주로 안전모드에서 잉크가
# 부족해 폴백된 small_instance) 점 주변 고정 비율 창을 크롭한다.
CONDITION_CROP_POINT_WINDOW_RATIO = 0.12

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_COMPLETENESS_MODEL = PROJECT_DIR / "completeness_rf.joblib"
DEFAULT_GLAZE_MODEL = PROJECT_DIR / "glaze_rf_v2.joblib"
# ai_hub/는 용량이 커서 git에 안 올리고 각자 로컬에 받아서 씀(.gitignore 참고).
# app/의 부모 폴더(저장소 루트) 기준 상대경로라 클론 위치가 달라도 동작함 -
# 팀원 로컬에 ai_hub/pottery_multitask_model_v2가 없으면 아래에서 자동으로
# 경고만 찍고 시대(CNN) 판정만 비활성화됨(다른 기능엔 영향 없음).
ERA_MODEL_DIR = PROJECT_DIR.parent / "ai_hub" / "pottery_multitask_model_v2"

# max_regions을 1보다 크게 잡아서, 사진 한 장에 여러 개의 별도 객체가
# 있는지(예: 발굴 현장에서 파편들을 늘어놓고 찍은 사진) 감지할 수 있게 한다.
# 실제 분석은 여전히 "사진 한 장 = 유물 한 점"을 전제로 하므로, 여러 개가
# 잡히면 분석을 진행하지 않고 재촬영을 안내한다 (analyze_pottery() 참고).
MAX_DETECTABLE_REGIONS = 5
detector = GroundedSAMDetector(
    text_prompt=POTTERY_TEXT_PROMPT, max_regions=MAX_DETECTABLE_REGIONS
)

try:
    completeness_clf = joblib.load(DEFAULT_COMPLETENESS_MODEL)
except FileNotFoundError:
    print(f"[경고] {DEFAULT_COMPLETENESS_MODEL} 없음 - 완전/파편 판정 비활성화")
    completeness_clf = None

try:
    glaze_clf = joblib.load(DEFAULT_GLAZE_MODEL)
except FileNotFoundError:
    print(f"[경고] {DEFAULT_GLAZE_MODEL} 없음 - 표면 광택 판정 비활성화")
    glaze_clf = None

try:
    era_cnn_model, era_class_names = load_era_cnn_model(ERA_MODEL_DIR)
except FileNotFoundError:
    print(f"[경고] {ERA_MODEL_DIR} 없음 - 시대(CNN) 판정 비활성화")
    era_cnn_model, era_class_names = None, None


def extract_completeness_features(mask: np.ndarray) -> dict[str, float] | None:
    import cv2

    binary_mask = np.asarray(mask, dtype=bool)
    contour = _largest_contour(binary_mask)
    if contour is None:
        return None
    area = cv2.contourArea(contour)
    if area == 0:
        return None
    return {
        "symmetry_iou": _symmetry_iou(binary_mask),
        "circularity": _circularity(contour, area),
        "max_defect_depth_ratio": _max_defect_depth_ratio(contour, binary_mask),
    }


_REGION_MERGE_OVERLAP_THRESHOLD = 0.5  # 이 비율 이상 겹치면 "같은 물체의 다른 탐지"로 보고 하나만 남긴다
_REGION_MERGE_PROXIMITY_RATIO = 0.03  # 두 영역 사이 간격이 이미지 대각선의 이 비율 이내면 안 겹쳐도 병합


def _mask_bbox(mask: np.ndarray):
    ys, xs = np.where(mask)
    if len(ys) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _bbox_gap(box_a, box_b) -> float:
    """두 bbox 사이의 최소 거리(픽셀). 겹치거나 맞닿아 있으면 0."""
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    dx = max(bx1 - ax2, ax1 - bx2, 0)
    dy = max(by1 - ay2, ay1 - by2, 0)
    return (dx**2 + dy**2) ** 0.5


def _deduplicate_overlapping_regions(regions: list[dict]) -> list[dict]:
    """SAM2 마스크 기준으로, 같은 물체로 보이는 영역은 하나만 남긴다.

    두 가지 경우를 각각 다른 기준으로 병합한다.

    ① 겹치는 경우: Grounding DINO가 유물 전체와 그 일부(목/몸통 등)를 각각
    별도 박스로 잡을 때 - 마스크가 크게 겹치면 하나로 본다.

    ② 겹치지 않지만 아주 가까이 붙어있는 경우: 유약이 반들반들한 도자기는
    강한 반사광 때문에 SAM2가 "여기서 물체가 끊긴다"고 잘못 판단해서, 물리
    적으로 하나인 그릇을 공간적으로 뚝 떨어진 두 덩어리로 쪼개 잡을 때가
    있다. 이 경우 두 마스크는 서로 안 겹치지만, bbox 사이 간격이 아주 좁다
    - 간격이 이미지 대각선의 일정 비율 이내면 같은 물체로 보고 합친다.
    """
    kept: list[dict] = []
    image_diag = None

    # 마스크 면적이 큰 순서로 처리 - 더 완전하게(전체를) 잡은 탐지를 우선 남긴다.
    sorted_regions = sorted(
        regions, key=lambda r: np.asarray(r["mask"]).sum(), reverse=True
    )

    for region in sorted_regions:
        mask = np.asarray(region["mask"], dtype=bool)
        mask_area = mask.sum()
        if mask_area == 0:
            continue

        if image_diag is None:
            h, w = mask.shape
            image_diag = (h**2 + w**2) ** 0.5

        bbox = _mask_bbox(mask)
        is_duplicate = False

        for kept_region in kept:
            kept_mask = np.asarray(kept_region["mask"], dtype=bool)

            # ① 겹침 기준
            overlap = np.logical_and(mask, kept_mask).sum()
            smaller_area = min(mask_area, kept_mask.sum())
            if (
                smaller_area > 0
                and overlap / smaller_area >= _REGION_MERGE_OVERLAP_THRESHOLD
            ):
                is_duplicate = True
                break

            # ② 근접 기준 - 안 겹쳐도 아주 가까우면 병합(반사광으로 인한 분절 대응)
            kept_bbox = _mask_bbox(kept_mask)
            if bbox is not None and kept_bbox is not None:
                gap = _bbox_gap(bbox, kept_bbox)
                if gap <= image_diag * _REGION_MERGE_PROXIMITY_RATIO:
                    is_duplicate = True
                    break

        if not is_duplicate:
            kept.append(region)

    return kept


_COLOR_SIMILARITY_THRESHOLD = 0.55  # cv2.compareHist(HISTCMP_CORREL) 기준, 1에 가까울수록 유사


def _region_color_histogram(image_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray | None:
    """영역 내부 픽셀만으로 HSV 색상 히스토그램을 계산한다.
    배경/그림자까지 섞이면 비교가 부정확해지므로 반드시 mask로 걸러낸다."""
    binary_mask = np.asarray(mask, dtype=np.uint8) * 255
    if binary_mask.sum() == 0:
        return None
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], binary_mask, [30, 32], [0, 180, 0, 256])
    cv2.normalize(hist, hist, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)
    return hist


def _group_regions_by_color_similarity(
    image_bgr: np.ndarray, regions: list[dict]
) -> list[dict[str, Any]]:
    """색상 분포가 비슷한 영역끼리 그룹으로 묶는다.

    주의: 이건 "같은 유물의 파편일 가능성"에 대한 아주 거친 힌트일 뿐이다.
    깨진 단면의 형태를 맞춰보는 진짜 재조립이 아니라 색·유약 톤만 비교한다 -
    우연히 색이 비슷한 서로 다른 유물도 묶일 수 있고, 반대로 같은 유물이어도
    조명 차이로 다르게 나올 수 있다. 참고용으로만 쓰고, 최종 판단은 조사자가
    사진을 직접 보고 내려야 한다.
    """
    histograms: list[np.ndarray | None] = [
        _region_color_histogram(image_bgr, region["mask"]) for region in regions
    ]

    parent = list(range(len(regions)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        root_i, root_j = find(i), find(j)
        if root_i != root_j:
            parent[root_j] = root_i

    for i in range(len(regions)):
        if histograms[i] is None:
            continue
        for j in range(i + 1, len(regions)):
            if histograms[j] is None:
                continue
            similarity = cv2.compareHist(histograms[i], histograms[j], cv2.HISTCMP_CORREL)
            if similarity >= _COLOR_SIMILARITY_THRESHOLD:
                union(i, j)

    groups: dict[int, list[int]] = defaultdict(list)
    for index in range(len(regions)):
        groups[find(index)].append(index)

    return [
        {
            "region_indices": [regions[i]["region_index"] for i in members],
            "member_count": len(members),
            "note": "색상 유사도 기반 추정 - 실제 접합 여부를 보장하지 않음",
        }
        for members in groups.values()
    ]


def predict_era(image_path: Path) -> dict[str, Any]:
    """문양(VLM)이랑 별개로, 시대만 로컬 CNN(무료)으로 예측한다.
    교차검증 정확도 88%로 문양(55~64%)/색상(33%)보다 훨씬 나았던
    부분이라 VLM으로 안 바꾸고 그대로 살렸다."""
    image = Image.open(image_path).convert("RGB")
    tensor = ERA_EVAL_TRANSFORM(image).unsqueeze(0)

    with torch.no_grad():
        outputs = era_cnn_model(tensor)

    probs = torch.softmax(outputs["era"], dim=1)[0]
    top_idx = int(probs.argmax())
    predicted_era = era_class_names["era"][top_idx]
    return {
        "prediction": predicted_era,
        "score": round(float(probs[top_idx]), 3),
        "interpretation": f"이미지 형태/양식 기준 {predicted_era} 후보(참고용)",
        "source": "CNN(로컬, 무료, 교차검증 정확도 88%)",
        "limitation": (
            "score는 분류기의 softmax 출력값이며 실제 정답 확률(보정된 확신도)이 "
            "아닙니다. 모델의 교차검증 정확도는 88%로, score가 높다고 해서 그보다 "
            "더 정확하다는 뜻은 아닙니다. 단일 사진의 형태·양식 기준 참고 결과로만 "
            "활용하세요."
        ),
    }


# 범례에서 "이 영역이 정밀 분할 결과가 아니라 근사치"라는 걸 한눈에 알 수
# 있도록 스코프별 안내 문구를 붙인다. 실사용 테스트에서 "SAM2가 상단띠를
# 정밀 분할했다"처럼 오해할 수 있다는 지적을 받아 추가했다 - 실제로는
# GDINO+SAM2는 유물 전체 실루엣만 잡고, 개별 문양 영역(대형 연속 문양의
# 사각형, 띠 문양의 잉크 기반 영역)은 그 실루엣 안에서 색상 대비·위치
# 규칙으로 추정한 결과다.
_SCOPE_LEGEND_SUFFIX = {
    "large_continuous": " (근사 주요 영역)",
    "border_band": " (문양 후보 영역·근사 추정)",
}


def _draw_legend(
    image: Image.Image,
    items: list[dict[str, Any]],
) -> Image.Image:
    if not items:
        return image

    font = load_font(15)
    seen: dict[str, tuple[tuple[int, int, int], bool, str | None, str | None]] = {}
    for item in items:
        badge = item.get("badge", "?")
        name = item.get("pattern_name", "?")
        label = f"{badge}  {name}"
        if label not in seen:
            seen[label] = (
                item["color"],
                item.get("name_in_reference_list", True),
                item.get("decision"),
                item.get("pattern_scope"),
            )

    if not seen:
        return image

    draw = ImageDraw.Draw(image)
    line_height = 20
    padding = 8
    swatch_width = 18

    def _label_text(
        label: str, in_reference: bool, decision: str | None, scope: str | None
    ) -> str:
        text = label
        if decision == "판정보류":
            text += " [판정보류]"
        if not in_reference:
            text += " [목록 외 명칭]"
        text += _SCOPE_LEGEND_SUFFIX.get(scope, "")
        return text

    text_widths = [
        draw.textbbox((0, 0), _label_text(label, in_ref, decision, scope), font=font)[2]
        for label, (_color, in_ref, decision, scope) in seen.items()
    ]
    box_width = max(text_widths, default=100) + padding * 2 + swatch_width + 6
    box_height = line_height * len(seen) + padding * 2
    x0 = max(0, image.width - box_width - 12)
    y0 = max(0, image.height - box_height - 12)

    draw.rectangle(
        [x0, y0, x0 + box_width, y0 + box_height],
        fill=(255, 255, 255, 235),
        outline=(0, 0, 0, 255),
        width=1,
    )

    for row, (label, (color, in_reference, decision, scope)) in enumerate(seen.items()):
        text_y = y0 + padding + row * line_height
        draw.rectangle(
            [x0 + padding, text_y + 3, x0 + padding + swatch_width, text_y + 15],
            fill=color + (255,),
            outline=(0, 0, 0, 255),
        )
        draw.text(
            (x0 + padding + swatch_width + 6, text_y),
            _label_text(label, in_reference, decision, scope),
            fill=(0, 0, 0, 255),
            font=font,
        )

    return image



def _draw_pattern_overlay(
    base_image: Image.Image,
    pattern_items: list[dict[str, Any]],
    visualization_mode: str = "markers",
) -> Image.Image:
    """문양 결과 시각화.

    markers:
        기본 조사 화면. 중심 마커와 짧은 배지만 표시한다.
        VLM bbox를 정밀 탐지 결과처럼 오해하지 않도록 큰 사각형/마스크는 숨긴다.
    detail:
        검토 화면. precise/regional 마스크와 approximate bbox를 함께 표시한다.
    """
    if visualization_mode not in {"markers", "detail"}:
        visualization_mode = "markers"

    mask_layer = Image.new("RGBA", base_image.size, (0, 0, 0, 0))
    annotation_layer = Image.new("RGBA", base_image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(annotation_layer)
    font = load_font(18)

    for item in pattern_items:
        color = item["color"]
        x, y = item["x"], item["y"]
        mask_status = item.get("mask_status", "approximate")

        if visualization_mode == "detail":
            if mask_status in {"precise", "regional"} and item.get("mask") is not None:
                pattern_scope = item.get("pattern_scope")
                density_map = item.get("density_map")
                if density_map is not None:
                    density_array = np.clip(
                        np.asarray(density_map, dtype=np.float32), 0.0, 1.0
                    )
                    mask_image = Image.fromarray(
                        (density_array * 255).astype(np.uint8), mode="L"
                    )
                else:
                    mask_image = Image.fromarray(
                        (np.asarray(item["mask"], dtype=bool) * 255).astype(np.uint8),
                        mode="L",
                    )

                # 채우기(fill)를 주된 시각적 신호로 쓰고 윤곽선은 보조로만 쓴다.
                # 예전엔 윤곽선이 진하고(alpha 150) 두꺼워서(width 2), border_band의
                # 잉크 기반 마스크가 스캘럽/붓선 하나하나를 다 외곽선으로 그리는
                # 바람에 "SAM으로 전체를 정밀 분할한 것"처럼 보인다는 지적을 받았다.
                # 지금은 스코프별로 옅은 반투명 채우기를 우선하고, 윤곽선은 얇고
                # 흐리게 낮춰 "여기 대략 이 영역"이라는 신호로만 쓴다.
                # - precise(small_instance): 그대로 진하게 (실제 잉크 연결요소 경계)
                # - regional/border_band: 채우기를 조금 더 진하게, 윤곽선은 얇고 옅게
                # - large_continuous(밀도 그라데이션): 원래도 "영역"이라기보다
                #   "이 안에 문양이 있다"는 신호였으므로 그대로 매우 옅게 유지
                if mask_status == "precise":
                    fill_alpha, line_alpha, line_width = 75, 230, 2
                elif pattern_scope == "border_band":
                    fill_alpha, line_alpha, line_width = 42, 110, 1
                else:
                    fill_alpha, line_alpha, line_width = 22, 90, 1
                colored = Image.new("RGBA", base_image.size, color + (fill_alpha,))
                transparent = Image.new("RGBA", base_image.size, (0, 0, 0, 0))
                mask_layer = Image.alpha_composite(
                    mask_layer,
                    Image.composite(colored, transparent, mask_image),
                )
                # 정밀(precise)이 아닌 경우, 잉크 노이즈 하나하나까지 윤곽선으로
                # 그리면 시각적으로 지저분해지므로 작은 조각은 걸러낸다.
                contour_min_area = 20 if mask_status == "precise" else 60
                for polygon in mask_to_contour_points(item["mask"], min_area=contour_min_area):
                    draw.line(
                        polygon + [polygon[0]],
                        fill=color + (line_alpha,),
                        width=line_width,
                    )
            else:
                approximate_box = item.get("approximate_box")
                if approximate_box is not None:
                    # approximate는 정상 탐지 박스와 구분되도록 점선으로 그린다.
                    x1, y1, x2, y2 = [int(v) for v in approximate_box]
                    dash = 12
                    gap = 7
                    for xx in range(x1, x2, dash + gap):
                        draw.line([(xx, y1), (min(xx + dash, x2), y1)], fill=color + (180,), width=3)
                        draw.line([(xx, y2), (min(xx + dash, x2), y2)], fill=color + (180,), width=3)
                    for yy in range(y1, y2, dash + gap):
                        draw.line([(x1, yy), (x1, min(yy + dash, y2))], fill=color + (180,), width=3)
                        draw.line([(x2, yy), (x2, min(yy + dash, y2))], fill=color + (180,), width=3)

        # 기본/상세 모두 실제 모델이 가장 일관되게 제공한 중심 위치만 표시한다.
        draw.ellipse(
            [x - 6, y - 6, x + 6, y + 6],
            fill=color + (255,),
            outline=(0, 0, 0, 255),
        )
        badge = item.get("badge", "P?")
        if mask_status == "approximate":
            badge = f"{badge}~"
        text_box = draw.textbbox((x + 9, y - 11), badge, font=font)
        padding = 3
        draw.rounded_rectangle(
            [
                text_box[0] - padding,
                text_box[1] - padding,
                text_box[2] + padding,
                text_box[3] + padding,
            ],
            radius=4,
            fill=(255, 255, 255, 225),
            outline=color + (255,),
            width=2,
        )
        draw.text((x + 9, y - 11), badge, fill=(0, 0, 0, 255), font=font)

    combined = Image.alpha_composite(base_image.convert("RGBA"), mask_layer)
    combined = Image.alpha_composite(combined, annotation_layer)
    combined = _draw_legend(combined, pattern_items)
    return combined.convert("RGB")


def render_selected_patterns(
    pattern_bundle: dict,
    selected_keys: list[str],
    visualization_mode: str = "markers",
) -> Image.Image:
    base_image = pattern_bundle["base_image"]
    all_patterns = pattern_bundle["patterns"]
    selected_items = [
        all_patterns[key]
        for key in selected_keys
        if key in all_patterns
    ]
    return _draw_pattern_overlay(
        base_image.copy(),
        selected_items,
        visualization_mode=visualization_mode,
    )


def expand_names_to_keys(
    pattern_bundle: dict,
    selected_names: list[str],
    include_low_confidence: bool = False,
) -> list[str]:
    by_name = pattern_bundle.get(
        "by_name_all" if include_low_confidence else "by_name_confirmed",
        {},
    )
    keys: list[str] = []
    for name in selected_names:
        keys.extend(by_name.get(name, []))
    return keys


def _prediction_probability(clf: Any, features: dict[str, float], columns: list[str]) -> tuple[Any, float]:
    matrix = [[features[column] for column in columns]]
    prediction = clf.predict(matrix)[0]
    probabilities = dict(zip(clf.classes_, clf.predict_proba(matrix)[0]))
    return prediction, float(max(probabilities.values()))


def _pattern_to_entry(pattern: PatternLocation, key: str) -> dict[str, Any]:
    return {
        "key": key,
        "pattern_family": pattern.pattern_family,
        "pattern_name": pattern.pattern_name,
        "display_name": pattern.display_name or pattern.pattern_name,
        "alternative_candidate": pattern.alternative_candidate,
        "visible_evidence": pattern.visible_evidence,
        "missing_evidence": pattern.missing_evidence,
        "decision": pattern.decision,
        "decision_votes": pattern.decision_votes,
        "name_votes": pattern.name_votes,
        "name_in_reference_list": pattern.name_in_reference_list,
        "pattern_scope": pattern.pattern_scope,
        "location_description": pattern.location_description,
        "x_percent": pattern.x_percent,
        "y_percent": pattern.y_percent,
        "bbox_percent": {
            "x1": pattern.bbox_x1_percent,
            "y1": pattern.bbox_y1_percent,
            "x2": pattern.bbox_x2_percent,
            "y2": pattern.bbox_y2_percent,
        },
        "confidence": pattern.confidence,
        "agreement_count": pattern.agreement_count,
        "agreement_total": pattern.agreement_total,
        "location_agreement": pattern.location_agreement,
        "name_agreement": pattern.name_agreement,
    }


def _make_badge(display_name: str, instance_number: int) -> str:
    prefix = _badge_prefix(display_name)
    return f"{prefix}{instance_number}"


def _badge_prefix(display_name: str | None) -> str:
    return display_name[:2] if display_name else "문양"


def _border_band_badge_base(pattern: PatternLocation) -> str:
    """border_band 배지는 실제 문양명 대신 위치(상단/하단) 기준으로 표시한다.

    정밀 마스킹이 안전 근사 모드로 대체된 상황에서는 "정확히 어떤 문양인가"보다
    "구연부 쪽 띠 하나, 굽 쪽 띠 하나가 있다"는 위치 구분이 조사자에게 더
    실용적이다. 실제 문양 분류(display_name)는 JSON/범례에 그대로 남는다.
    """
    position = "상단" if pattern.y_percent < 50 else "하단"
    return f"{position}띠"


def _is_default_visible(pattern: PatternLocation, min_pattern_agreement: int) -> bool:
    """기본 화면(마커만 보이는 조사 화면)에 표시할지 여부.

    합의 횟수(agreement_count)만 보던 기존 조건에, decision != 판정보류
    조건을 추가했다. 합의는 재현성(같은 답이 반복됐는지)일 뿐 정확성을
    보장하지 않으므로, 근거가 부족해 판정보류로 내려간 항목은 합의 횟수가
    높더라도 기본 화면에서는 숨기고 상세 JSON에만 남긴다.
    """
    return (
        (pattern.agreement_count or 0) >= min_pattern_agreement
        and pattern.decision != "판정보류"
    )


SAFE_MODE_INK_TIGHTEN_MIN_PIXELS = 16
SAFE_MODE_INK_TIGHTEN_PADDING_RATIO = 0.04


def _tighten_box_to_ink(
    box: list[int] | tuple[int, int, int, int],
    ink_mask: np.ndarray,
    artifact_mask: np.ndarray,
    exclusion_mask: np.ndarray | None = None,
) -> list[int] | None:
    """느슨한 VLM bbox를 실제 잉크(무늬) 픽셀 범위로 축소한다.

    exclusion_mask를 주면(주로 claimed_mask - 다른 문양이 이미 차지한 영역)
    그 영역의 픽셀은 잉크로 치지 않는다. large_continuous(용 등)를 안전
    모드로 타이트닝할 때 border_band가 이미 차지한 구연부/굽 영역을 넘겨주면,
    대형 문양의 박스가 그 영역으로 파고들지 않는다.
    """
    x1, y1, x2, y2 = box
    height, width = ink_mask.shape
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(width, int(x2)), min(height, int(y2))
    if x2 <= x1 or y2 <= y1:
        return None

    region_ink = np.logical_and(
        ink_mask[y1:y2, x1:x2], artifact_mask[y1:y2, x1:x2]
    )
    if exclusion_mask is not None:
        region_ink = np.logical_and(
            region_ink, np.logical_not(exclusion_mask[y1:y2, x1:x2])
        )
    if int(region_ink.sum()) < SAFE_MODE_INK_TIGHTEN_MIN_PIXELS:
        return None

    ys, xs = np.where(region_ink)
    pad_x = max(4, int((x2 - x1) * SAFE_MODE_INK_TIGHTEN_PADDING_RATIO))
    pad_y = max(4, int((y2 - y1) * SAFE_MODE_INK_TIGHTEN_PADDING_RATIO))
    tight_x1 = max(x1, x1 + int(xs.min()) - pad_x)
    tight_y1 = max(y1, y1 + int(ys.min()) - pad_y)
    tight_x2 = min(x2, x1 + int(xs.max()) + 1 + pad_x)
    tight_y2 = min(y2, y1 + int(ys.max()) + 1 + pad_y)
    return [tight_x1, tight_y1, tight_x2, tight_y2]



def _normalize_gloss_prediction(raw_prediction: Any) -> tuple[str, str]:
    """기존 RF 라벨을 재료 판정(유약/무유)이 아닌 시각적 광택 수준으로 표현한다."""
    raw = str(raw_prediction).strip()
    high_labels = {"유광", "glossy", "high", "1", "True"}
    low_labels = {"무유", "무광", "matte", "low", "0", "False"}
    if raw in high_labels:
        return "높음", "사진에서 강한 표면 반사가 관찰되는 유형"
    if raw in low_labels:
        return "낮음", "사진에서 표면 반사가 약하게 관찰되는 유형"
    return "판단 보류", f"기존 모델 라벨({raw})을 광택 수준으로 안전하게 변환하지 못함"


def _is_invalid_small_instance_bbox(entry: dict[str, Any]) -> bool:
    """small_instance인데 bbox가 사진의 큰 영역을 차지하면 위치 근사치로도 쓰지 않는다."""
    if entry.get("pattern_scope") != "small_instance":
        return False
    box = entry.get("bbox_percent") or {}
    try:
        w = float(box["x2"]) - float(box["x1"])
        h = float(box["y2"]) - float(box["y1"])
    except (KeyError, TypeError, ValueError):
        return False
    return w > 30.0 or h > 30.0 or (w * h) > 8.0 * 100.0


def _build_condition_crop_box(
    box: list[int] | tuple[int, int, int, int] | None,
    point: tuple[int, int],
    scope: str,
    image_width: int,
    image_height: int,
) -> list[int]:
    """문양 클로즈업 육안상태조사용 크롭 박스를 계산한다.

    렌더링용 박스와 달리 "정확한 경계"일 필요가 없다 - 문양을 넉넉하게
    포함하기만 하면 되므로, 이미 있는 근사 박스(있으면)에 스코프별 여백을
    더하고, 없으면(점 정보만 있는 경우) 점 주변 고정 비율 창을 사용한다.
    이 낮은 정밀도 요구가 바로 이 방식이 기존 정밀 마스킹보다 안정적인
    이유다 - 박스가 다소 헐렁해도 클로즈업 상태 평가에는 문제가 없다.
    """
    if box is not None:
        x1, y1, x2, y2 = [int(v) for v in box]
        pad_ratio = CONDITION_CROP_PADDING_RATIO.get(scope, 0.15)
        pad_x = max(int((x2 - x1) * pad_ratio), 10)
        pad_y = max(int((y2 - y1) * pad_ratio), 10)
        x1, y1, x2, y2 = x1 - pad_x, y1 - pad_y, x2 + pad_x, y2 + pad_y
    else:
        x, y = point
        half = max(
            int(max(image_width, image_height) * CONDITION_CROP_POINT_WINDOW_RATIO),
            CONDITION_CROP_MIN_SIDE_PX // 2,
        )
        x1, y1, x2, y2 = x - half, y - half, x + half, y + half

    if x2 - x1 < CONDITION_CROP_MIN_SIDE_PX:
        cx = (x1 + x2) // 2
        x1, x2 = cx - CONDITION_CROP_MIN_SIDE_PX // 2, cx + CONDITION_CROP_MIN_SIDE_PX // 2
    if y2 - y1 < CONDITION_CROP_MIN_SIDE_PX:
        cy = (y1 + y2) // 2
        y1, y2 = cy - CONDITION_CROP_MIN_SIDE_PX // 2, cy + CONDITION_CROP_MIN_SIDE_PX // 2

    x1 = max(0, min(x1, image_width - 1))
    y1 = max(0, min(y1, image_height - 1))
    x2 = max(x1 + 1, min(x2, image_width))
    y2 = max(y1 + 1, min(y2, image_height))
    return [x1, y1, x2, y2]


def _run_condition_review(
    base_image: Image.Image,
    pattern_items_by_key: dict[str, dict[str, Any]],
    default_keys: list[str],
    scope_by_key: dict[str, str],
    image_width: int,
    image_height: int,
) -> tuple[dict[str, dict[str, Any]], dict[str, Image.Image]]:
    """확정된(default_keys) 문양마다 클로즈업 크롭을 만들고 상태를 평가한다.

    반환값은 (key -> 상태 평가 결과 dict, key -> 크롭 이미지) 두 딕셔너리.
    ENABLE_CONDITION_REVIEW가 꺼져 있거나 확정된 문양이 없으면 빈 딕셔너리를
    반환한다. 개별 호출 실패는 해당 key의 결과에 에러로 남기고 나머지는
    계속 진행한다.
    """
    condition_by_key: dict[str, dict[str, Any]] = {}
    crop_by_key: dict[str, Image.Image] = {}
    if not ENABLE_CONDITION_REVIEW or not default_keys:
        return condition_by_key, crop_by_key

    base_rgb = base_image.convert("RGB")
    jobs: list[tuple[str, Image.Image, str, str]] = []
    for key in default_keys:
        item = pattern_items_by_key.get(key)
        if item is None:
            continue
        scope = scope_by_key.get(key, "small_instance")
        box = item.get("approximate_box")
        if box is None and item.get("mask") is not None:
            box = mask_bbox(item["mask"])
        crop_box = _build_condition_crop_box(
            box, (item["x"], item["y"]), scope, image_width, image_height
        )
        crop_image = base_rgb.crop(tuple(crop_box))
        crop_by_key[key] = crop_image
        jobs.append(
            (key, crop_image, item.get("pattern_name", "문양"), item.get("decision") or "추정")
        )

    results = assess_pattern_conditions_in_parallel(jobs)
    for key, outcome in results.items():
        if isinstance(outcome, Exception):
            condition_by_key[key] = {"status": "평가 실패", "error": str(outcome)}
        else:
            condition_by_key[key] = {
                "condition_status": outcome.condition_status,
                "issues": [
                    {
                        "issue_type": issue.issue_type,
                        "confidence": issue.confidence,
                        "alternative_explanation": issue.alternative_explanation,
                    }
                    for issue in outcome.issues
                ],
                "condition_description": outcome.condition_description,
                "human_review_required": outcome.human_review_required,
                "confidence": outcome.confidence,
            }

    return condition_by_key, crop_by_key


def analyze_pottery(
    image_path: str,
    use_vlm_pattern: bool = DEFAULT_USE_VLM_PATTERN_ANALYSIS,
    n_calls: int = 3,
    treat_as_single_artifact: bool = False,
) -> tuple[dict[str, Any], Image.Image | None, dict | None]:
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(image_path)

    regions = detector.predict(image_path=str(path))
    if not regions:
        return (
            {
                "status": "탐지 실패",
                "reason": "Grounding DINO + SAM2가 도자기 영역을 찾지 못함",
            },
            None,
            None,
        )

    # 박스 기준 중복 제거를 통과했더라도, 같은 유물의 부분(목/몸통 등)이
    # 별도 영역으로 잡혔을 수 있으므로 실제 마스크 겹침으로 한 번 더 정리한다.
    regions = _deduplicate_overlapping_regions(regions)

    if len(regions) > 1 and not treat_as_single_artifact:
        # 발굴 현장에서 흔한 "여러 조각을 늘어놓고 찍은 사진" 케이스.
        # 이 파이프라인은 사진 한 장 = 유물 한 점을 전제로 완전/파편·유약·
        # 시대·문양을 계산하므로, 여러 영역이 잡히면 그중 하나를 임의로
        # 골라 분석하는 대신 재촬영을 안내하고 분석을 중단한다. 조용히
        # 하나만 골라 분석하면, 그 결과가 사진 전체를 대표하는 것처럼
        # 오인될 위험이 크다.
        #
        # 다만 "서로 다른 유물 여러 점"과 "하나의 유물이 깨져서 흩어진
        # 조각들"은 사진만으로 구분이 근본적으로 애매하다(마스크가 안
        # 닿아있다고 반드시 다른 유물인 건 아니다). 그래서 여기서 자동으로
        # 판단하지 않고, 사용자에게 확인을 받는다 - 사용자가 "하나의 유물이
        # 깨진 조각들"이라고 확인하면 treat_as_single_artifact=True로 재요청이
        # 들어오고, 그때는 아래 블록에서 모든 영역을 하나로 합쳐 분석한다.
        image_bgr_multi = imread_unicode_safe(path)
        region_groups = (
            _group_regions_by_color_similarity(image_bgr_multi, regions)
            if image_bgr_multi is not None
            else None
        )

        group_note = ""
        if region_groups and len(region_groups) < len(regions):
            group_note = (
                f" 색상 유사도로 보면 {len(region_groups)}개 그룹으로 나뉘어 보이는데, "
                "이건 접합면을 본 게 아니라 색상만 비교한 거친 추정이라 확정 근거로 쓰면 안 됩니다."
            )

        return (
            {
                "status": "다중 객체 감지",
                "reason": (
                    f"이 사진에서 서로 떨어진 객체가 {len(regions)}개 감지되었습니다. "
                    "여러 파편이나 여러 점의 유물을 한 사진에 늘어놓고 촬영하신 것으로 "
                    "보입니다. 이 분석기는 사진 한 장에 유물(또는 파편) 하나만 있는 "
                    "것을 전제로 합니다 - 파편별로 한 장씩 나눠서 다시 촬영해주세요. "
                    "만약 이 조각들이 전부 하나의 유물이 깨진 것이라면, 그렇게 확인하고 "
                    "이 사진 그대로 분석을 진행할 수도 있습니다."
                    + group_note
                ),
                "detected_region_count": len(regions),
                "region_groups": region_groups,
            },
            None,
            None,
        )

    if len(regions) > 1:
        # treat_as_single_artifact=True로 재요청된 경우 - 사용자가 이미
        # "하나의 유물이 깨진 조각들"이라고 확인했으므로, 감지된 모든 영역의
        # 마스크를 합쳐 하나의 유물로 취급한다. 조각 사이 빈 공간(원래
        # 있었어야 할 부분)도 완전/파편 판정 계산에 자연스럽게 결손으로
        # 반영된다 - 조각들을 이어붙인 하나의 외곽선으로 보기 때문이다.
        artifact_mask = np.zeros_like(np.asarray(regions[0]["mask"], dtype=bool))
        for region in regions:
            artifact_mask |= np.asarray(region["mask"], dtype=bool)
    else:
        artifact_mask = np.asarray(regions[0]["mask"], dtype=bool)

    artifact_area = int(artifact_mask.sum())
    if artifact_area == 0:
        return {"status": "탐지 실패", "reason": "도자기 마스크 면적이 0"}, None, None

    image_bgr = imread_unicode_safe(path)
    if image_bgr is None:
        return {"status": "이미지 로드 실패"}, None, None

    base_image = Image.open(path).convert("RGBA")
    width, height = base_image.size
    result: dict[str, Any] = {"status": "성공"}
    if len(regions) > 1:
        # 이후 어디서 이 결과를 보든(화면, 보고서) "이건 여러 조각을
        # 하나로 합쳐서 분석한 결과"라는 걸 알 수 있게 표시해둔다.
        result["merged_fragment_count"] = len(regions)
    pattern_bundle: dict[str, Any] | None = None

    ink_mask = compute_ink_mask(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB), artifact_mask)

    if completeness_clf is not None:
        features = extract_completeness_features(artifact_mask)
        if features is not None:
            prediction, score = _prediction_probability(
                completeness_clf,
                features,
                COMPLETENESS_FEATURES,
            )
            result["completeness"] = {
                "prediction": prediction,
                "score": round(score, 3),
                "interpretation": f"사진상 외형 기준 {prediction} 추정",
                "features": {key: round(value, 4) for key, value in features.items()},
                "limitation": "단일 사진 기준으로 뒷면·내부·미세 결손은 확인할 수 없음",
            }
        else:
            result["completeness"] = {
                "prediction": "판단 보류",
                "reason": "윤곽선 추출 실패",
            }
    else:
        result["completeness"] = {"prediction": "모델 없음"}

    if glaze_clf is not None:
        estimate = estimate_glaze(image_bgr, artifact_mask)
        glaze_features = {
            "highlight_area_ratio": estimate.highlight_area_ratio,
            "highlight_kurtosis": estimate.highlight_kurtosis,
            "saturation_std": estimate.saturation_std,
        }
        prediction, score = _prediction_probability(
            glaze_clf,
            glaze_features,
            GLAZE_FEATURES,
        )
        gloss_level, gloss_explanation = _normalize_gloss_prediction(prediction)
        result["glaze"] = {
            "prediction": gloss_level,
            "raw_model_label": str(prediction),
            "score": round(score, 3),
            "interpretation": f"표면 광택 수준: {gloss_level}",
            "explanation": gloss_explanation,
            "features": {key: round(value, 4) for key, value in glaze_features.items()},
            "limitation": (
                "사진에서 보이는 반사 특성만 추정합니다. 유약의 실제 존재 여부나 "
                "제작기법은 단일 RGB 사진만으로 확정하지 않습니다."
            ),
        }
    else:
        result["glaze"] = {"prediction": "모델 없음"}

    if era_cnn_model is not None:
        try:
            result["era"] = predict_era(path)
        except Exception as era_error:
            print(f"[경고] 시대(CNN) 예측 실패: {era_error}")
            result["era"] = {"prediction": "예측 실패", "error": str(era_error)}
    else:
        result["era"] = {"prediction": "모델 없음"}

    # 시대 자체는 그대로 CNN이 판단하되(정확도가 더 높으므로), 그 판단에
    # 사람이 검증할 수 있는 근거를 VLM으로 붙인다. use_vlm_pattern과 같은
    # 스위치를 재사용한다 - 이것도 유료 VLM 호출이라, 비용을 아끼고 싶으면
    # (use_vlm_pattern=False) 문양 분석과 함께 꺼지게 하는 게 자연스럽다.
    if use_vlm_pattern and result["era"].get("prediction") not in (
        None,
        "모델 없음",
        "예측 실패",
    ):
        try:
            evidence = explain_era_prediction(str(path), result["era"]["prediction"])
            result["era"]["evidence"] = evidence.model_dump()
        except Exception as evidence_error:
            print(f"[경고] 시대 판단 근거 조회 실패: {evidence_error}")
            result["era"]["evidence"] = {"error": str(evidence_error)}

    if not use_vlm_pattern:
        result["pattern_era_color"] = None
        return result, base_image.convert("RGB"), None

    try:
        vlm_result = analyze_pottery_patterns_ensemble(str(path), n_calls=n_calls)
        min_pattern_agreement = getattr(
            vlm_result, "min_agreement_used", FALLBACK_MIN_PATTERN_AGREEMENT
        )
        unique_names = sorted({pattern.pattern_name for pattern in vlm_result.patterns})
        color_map = {
            name: BADGE_COLORS[index % len(BADGE_COLORS)]
            for index, name in enumerate(unique_names)
        }

        expanded_artifact = expand_artifact_mask(artifact_mask, kernel_size=15)
        entries_by_index: dict[int, dict[str, Any]] = {}
        pattern_items_by_key: dict[str, dict[str, Any]] = {}
        default_keys: list[str] = []
        name_instance_counter: dict[str, int] = defaultdict(int)
        # 클로즈업 육안상태조사용 크롭 박스를 나중에 계산할 때 스코프별로 다른
        # 여백을 줘야 하므로(border_band는 좁게, small_instance는 넉넉하게)
        # 문양별 pattern_scope를 key 기준으로 따로 기록해둔다.
        scope_by_key: dict[str, str] = {}

        claimed_mask = np.zeros((height, width), dtype=bool)
        # border_band는 항상 실제 마스킹 경로를 타야 하므로(아래 참고) large_continuous보다
        # 먼저 처리해 claimed_mask에 구연부/굽 영역이 먼저 반영되게 한다.
        processing_order = sorted(
            range(len(vlm_result.patterns)),
            key=lambda i: 1 if vlm_result.patterns[i].pattern_scope == "large_continuous" else 0,
        )

        for index in processing_order:
            pattern = vlm_result.patterns[index]
            key = f"{pattern.pattern_name}__{index}"
            scope_by_key[key] = pattern.pattern_scope
            entry = _pattern_to_entry(pattern, key)
            display_name = pattern.display_name or pattern.pattern_name
            if pattern.pattern_scope == "border_band":
                # "상단띠"/"하단띠"는 그 자체로 2글자를 넘는 고유 접두어이므로
                # 일반 _badge_prefix()의 2글자 절삭을 거치지 않고 그대로 배지
                # 접두어로 쓴다 (사용자가 원한 "상단띠1"/"하단띠1" 형태).
                badge_prefix = _border_band_badge_base(pattern)
            elif pattern.decision == "판정보류":
                badge_prefix = _badge_prefix("문양")
            else:
                badge_prefix = _badge_prefix(display_name)
            name_instance_counter[badge_prefix] += 1
            badge = f"{badge_prefix}{name_instance_counter[badge_prefix]}"
            entry["badge"] = badge

            original_x = int(pattern.x_percent / 100 * width)
            original_y = int(pattern.y_percent / 100 * height)
            color = color_map[pattern.pattern_name]

            # border_band는 SAFE_APPROXIMATE_MASKING_ONLY 여부와 무관하게 항상 실제
            # build_border_band_mask()를 사용한다. 안전 모드가 필요했던 건
            # large_continuous의 예전 잉크 추적 마스킹이었지 border_band가 아니었는데,
            # 같은 플래그로 묶여서 근사 사각형으로 대체되는 바람에 exclusion_zone이
            # claimed_mask에 반영되지 못하던 문제를 고쳤다.
            if pattern.pattern_scope == "border_band":
                try:
                    mask_result = build_border_band_mask(pattern, artifact_mask, ink_mask)
                    band_box = mask_bbox(mask_result.mask)
                    if band_box is None:
                        raise RuntimeError("띠 영역 bbox 계산 실패")
                    x = (band_box[0] + band_box[2]) // 2
                    y = (band_box[1] + band_box[3]) // 2
                    entry.update(
                        {
                            "used_point": {"x": x, "y": y},
                            "snap_distance_px": None,
                            "mask_status": "regional",
                            "mask_method": mask_result.method,
                            "mask_area_ratio_artifact": round(mask_result.artifact_area_ratio, 4),
                            "mask_area_ratio_image": round(mask_result.image_area_ratio, 4),
                            "sam_score": None,
                            "is_approximate": False,
                            "interpretation": "유물 실루엣 기준 띠 영역 추정",
                        }
                    )
                    pattern_items_by_key[key] = {
                        "mask": mask_result.mask,
                        "color": color,
                        "x": x,
                        "y": y,
                        "pattern_name": display_name,
                        "confidence": pattern.confidence,
                        "mask_status": "regional",
                        "pattern_scope": pattern.pattern_scope,
                        "badge": badge,
                        "name_in_reference_list": pattern.name_in_reference_list,
                        "decision": pattern.decision,
                    }
                    claim_region = (
                        mask_result.exclusion_zone
                        if mask_result.exclusion_zone is not None
                        else mask_result.mask
                    )
                    claimed_mask = np.logical_or(claimed_mask, claim_region)
                except Exception as band_error:
                    approximate_box = percent_bbox_to_pixels(pattern, width, height)
                    if approximate_box is not None:
                        clipped_box = clip_box_to_artifact(approximate_box, artifact_mask)
                    else:
                        clipped_box = None
                    x = max(0, min(width - 1, original_x))
                    y = max(0, min(height - 1, original_y))
                    entry.update(
                        {
                            "used_point": {"x": x, "y": y},
                            "snap_distance_px": None,
                            "mask_status": "approximate",
                            "mask_area_ratio_artifact": None,
                            "mask_area_ratio_image": None,
                            "sam_score": None,
                            "is_approximate": True,
                            "mask_error": str(band_error),
                        }
                    )
                    pattern_items_by_key[key] = {
                        "mask": None,
                        "color": color,
                        "x": x,
                        "y": y,
                        "pattern_name": display_name,
                        "confidence": pattern.confidence,
                        "mask_status": "approximate",
                        "pattern_scope": pattern.pattern_scope,
                        "approximate_box": clipped_box,
                        "badge": badge,
                        "name_in_reference_list": pattern.name_in_reference_list,
                        "decision": pattern.decision,
                    }
                    if clipped_box is not None:
                        claimed_mask_box = np.zeros((height, width), dtype=bool)
                        cx1, cy1, cx2, cy2 = clipped_box
                        claimed_mask_box[cy1:cy2, cx1:cx2] = True
                        claimed_mask = np.logical_or(claimed_mask, claimed_mask_box)

                entries_by_index[index] = entry
                if _is_default_visible(pattern, min_pattern_agreement):
                    default_keys.append(key)
                continue

            if SAFE_APPROXIMATE_MASKING_ONLY:
                if _is_invalid_small_instance_bbox(entry):
                    entry.update(
                        {
                            "mask_status": "excluded",
                            "mask_area_ratio_artifact": None,
                            "mask_area_ratio_image": None,
                            "sam_score": None,
                            "excluded_reason": (
                                "small_instance로 분류됐지만 bbox가 지나치게 커서 "
                                "개별 문양 위치로 사용할 수 없음"
                            ),
                        }
                    )
                    entries_by_index[index] = entry
                    continue

                approximate_box = (
                    percent_bbox_to_pixels(pattern, width, height)
                    if pattern.pattern_scope != "small_instance"
                    else None
                )
                clipped_box = (
                    clip_box_to_artifact(approximate_box, artifact_mask)
                    if approximate_box is not None
                    else None
                )
                if clipped_box is not None:
                    is_large_continuous = pattern.pattern_scope == "large_continuous"
                    tightened_box = _tighten_box_to_ink(
                        clipped_box,
                        ink_mask,
                        artifact_mask,
                        exclusion_mask=claimed_mask if is_large_continuous else None,
                    )
                    if tightened_box is not None:
                        clipped_box = tightened_box
                if clipped_box is not None:
                    x = (clipped_box[0] + clipped_box[2]) // 2
                    y = (clipped_box[1] + clipped_box[3]) // 2
                else:
                    snapped = snap_point_to_artifact(
                        artifact_mask, original_x, original_y, max_distance_ratio=0.05
                    )
                    if snapped is not None:
                        x, y, _snap_distance = snapped
                    else:
                        x = max(0, min(width - 1, original_x))
                        y = max(0, min(height - 1, original_y))
                entry.update(
                    {
                        "used_point": {"x": x, "y": y},
                        "mask_status": "approximate",
                        "mask_area_ratio_artifact": None,
                        "mask_area_ratio_image": None,
                        "sam_score": None,
                        "is_approximate": True,
                        "interpretation": "안전 모드: 정밀 마스킹 대신 근사 사각형만 표시",
                    }
                )
                pattern_items_by_key[key] = {
                    "mask": None,
                    "color": color,
                    "x": x,
                    "y": y,
                    "pattern_name": display_name,
                    "confidence": pattern.confidence,
                    "mask_status": "approximate",
                    "pattern_scope": pattern.pattern_scope,
                    "approximate_box": clipped_box,
                    "approx_radius": 20,
                    "badge": badge,
                    "name_in_reference_list": pattern.name_in_reference_list,
                    "decision": pattern.decision,
                }
                entries_by_index[index] = entry
                if pattern.pattern_scope == "large_continuous" and clipped_box is not None:
                    claimed_mask_box = np.zeros((height, width), dtype=bool)
                    cx1, cy1, cx2, cy2 = clipped_box
                    claimed_mask_box[cy1:cy2, cx1:cx2] = True
                    claimed_mask = np.logical_or(claimed_mask, claimed_mask_box)
                if _is_default_visible(pattern, min_pattern_agreement):
                    default_keys.append(key)
                continue

            in_expanded = (
                0 <= original_y < expanded_artifact.shape[0]
                and 0 <= original_x < expanded_artifact.shape[1]
                and expanded_artifact[original_y, original_x]
            )
            snapped = snap_point_to_artifact(
                artifact_mask,
                original_x,
                original_y,
                max_distance_ratio=0.05 if in_expanded else 0.035,
            )
            if snapped is None:
                approximate_box = percent_bbox_to_pixels(pattern, width, height)
                if pattern.pattern_scope == "large_continuous" and approximate_box is not None:
                    clipped_box = clip_box_to_artifact(approximate_box, artifact_mask)
                    x = (clipped_box[0] + clipped_box[2]) // 2
                    y = (clipped_box[1] + clipped_box[3]) // 2
                    entry.update(
                        {
                            "used_point": {"x": x, "y": y},
                            "snap_distance_px": None,
                            "mask_status": "approximate",
                            "mask_area_ratio_artifact": None,
                            "mask_area_ratio_image": None,
                            "sam_score": None,
                            "is_approximate": True,
                            "mask_error": "중심점 좌표 보정 실패 - 유물 내부 bbox 근사 표시",
                        }
                    )
                    pattern_items_by_key[key] = {
                        "mask": None,
                        "color": color,
                        "x": x,
                        "y": y,
                        "pattern_name": display_name,
                        "confidence": pattern.confidence,
                        "mask_status": "approximate",
                        "pattern_scope": pattern.pattern_scope,
                        "approximate_box": clipped_box,
                        "badge": badge,
                        "name_in_reference_list": pattern.name_in_reference_list,
                        "decision": pattern.decision,
                    }
                    entries_by_index[index] = entry
                    if _is_default_visible(pattern, min_pattern_agreement):
                        default_keys.append(key)
                    continue

                entry.update(
                    {
                        "mask_status": "excluded",
                        "mask_area_ratio_artifact": None,
                        "mask_area_ratio_image": None,
                        "sam_score": None,
                        "excluded_reason": "유물 영역에서 너무 먼 좌표",
                    }
                )
                entries_by_index[index] = entry
                continue

            x, y, snap_distance = snapped
            entry["used_point"] = {"x": x, "y": y}
            entry["snap_distance_px"] = round(snap_distance, 1)

            is_large_continuous = pattern.pattern_scope == "large_continuous"

            try:
                mask_result = get_pattern_mask(
                    pattern=pattern,
                    artifact_mask=artifact_mask,
                    image_width=width,
                    image_height=height,
                    x=x,
                    y=y,
                    ink_mask=ink_mask,
                    exclusion_mask=claimed_mask if is_large_continuous else None,
                )
                status = "precise" if pattern.pattern_scope == "small_instance" else "regional"
                entry.update(
                    {
                        "mask_status": status,
                        "mask_method": mask_result.method,
                        "mask_area_ratio_artifact": round(mask_result.artifact_area_ratio, 4),
                        "mask_area_ratio_image": round(mask_result.image_area_ratio, 4),
                        "sam_score": (
                            round(mask_result.sam_score, 3)
                            if mask_result.sam_score is not None
                            else None
                        ),
                        "is_approximate": False,
                        "interpretation": (
                            "국소 문양 경계 후보"
                            if status == "precise"
                            else "대형 문양 관련 장식 영역 추정"
                        ),
                    }
                )
                pattern_items_by_key[key] = {
                    "mask": mask_result.mask,
                    "color": color,
                    "x": x,
                    "y": y,
                    "pattern_name": display_name,
                    "confidence": pattern.confidence,
                    "mask_status": status,
                    "pattern_scope": pattern.pattern_scope,
                    "badge": badge,
                    "name_in_reference_list": pattern.name_in_reference_list,
                    "decision": pattern.decision,
                    "density_map": getattr(mask_result, "density_map", None),
                }
                claimed_mask = np.logical_or(claimed_mask, mask_result.mask)
            except Exception as mask_error:
                approximate_box = percent_bbox_to_pixels(pattern, width, height)
                clipped_box = (
                    clip_box_to_artifact(approximate_box, artifact_mask)
                    if approximate_box is not None
                    else None
                )
                entry.update(
                    {
                        "mask_status": "approximate",
                        "mask_area_ratio_artifact": None,
                        "mask_area_ratio_image": None,
                        "sam_score": None,
                        "is_approximate": True,
                        "mask_error": str(mask_error),
                    }
                )
                pattern_items_by_key[key] = {
                    "mask": None,
                    "color": color,
                    "x": x,
                    "y": y,
                    "pattern_name": display_name,
                    "confidence": pattern.confidence,
                    "mask_status": "approximate",
                    "pattern_scope": pattern.pattern_scope,
                    "badge": badge,
                    "approximate_box": clipped_box,
                    "approx_radius": 20,
                    "name_in_reference_list": pattern.name_in_reference_list,
                    "decision": pattern.decision,
                }

            entries_by_index[index] = entry
            if _is_default_visible(pattern, min_pattern_agreement):
                default_keys.append(key)

        patterns_out: list[dict[str, Any]] = [
            entries_by_index[i]
            for i in range(len(vlm_result.patterns))
            if i in entries_by_index
        ]

        default_items = [
            pattern_items_by_key[key]
            for key in default_keys
            if key in pattern_items_by_key
        ]
        output_image = _draw_pattern_overlay(
            base_image.copy(),
            default_items,
            visualization_mode="markers",
        )

        # 클로즈업 육안상태조사(v9): 확정된 문양마다 넉넉한 크롭을 만들어
        # VLM에 다시 한번 보여주고 보존 상태(마모/박락/변색/균열/오염)를
        # 단일 호출로 평가한다. 정밀 마스크/박스를 맞추려던 기존 시도보다
        # 안정적인 이유는 크롭 박스가 "넉넉하기만" 하면 되기 때문이다.
        condition_by_key, crop_by_key = _run_condition_review(
            base_image, pattern_items_by_key, default_keys, scope_by_key, width, height
        )
        for key, condition in condition_by_key.items():
            if key in pattern_items_by_key:
                pattern_items_by_key[key]["condition"] = condition
            if key in crop_by_key:
                pattern_items_by_key[key]["condition_crop"] = crop_by_key[key]

        by_name_all: dict[str, list[str]] = defaultdict(list)
        by_name_confirmed: dict[str, list[str]] = defaultdict(list)
        default_names: set[str] = set()
        for key, item in pattern_items_by_key.items():
            by_name_all[item["pattern_name"]].append(key)
            if key in default_keys:
                by_name_confirmed[item["pattern_name"]].append(key)
                default_names.add(item["pattern_name"])

        pattern_bundle = {
            "base_image": base_image,
            "patterns": pattern_items_by_key,
            "by_name_all": dict(by_name_all),
            "by_name_confirmed": dict(by_name_confirmed),
            "by_name": dict(by_name_confirmed),
            "default_keys": default_keys,
            "default_names": sorted(default_names),
            "condition_gallery": [
                {
                    "key": key,
                    "badge": pattern_items_by_key[key].get("badge"),
                    "pattern_name": pattern_items_by_key[key].get("pattern_name"),
                    "crop": crop_by_key[key],
                    "condition": condition_by_key.get(key, {}),
                }
                for key in default_keys
                if key in crop_by_key
            ],
        }

        for out_entry in patterns_out:
            entry_key = out_entry.get("key")
            if entry_key in condition_by_key:
                out_entry["condition"] = condition_by_key[entry_key]

        result["pattern_era_color"] = {
            "overall_description": vlm_result.overall_description,
            "patterns": patterns_out,
            "uncertainty": vlm_result.uncertainty,
            "min_agreement_used": min_pattern_agreement,
            "calls_requested": getattr(vlm_result, "calls_requested", n_calls),
            "calls_succeeded": getattr(vlm_result, "calls_succeeded", None),
            "display_policy": (
                f"기본 화면에는 {min_pattern_agreement}회 이상 위치 합의되고 "
                "판정보류가 아닌 문양만 표시"
            ),
            "condition_review_enabled": ENABLE_CONDITION_REVIEW,
        }
    except Exception as error:
        print(f"[VLM 문양 분석 실패] {error}")
        result["pattern_era_color"] = {"error": str(error)}
        output_image = base_image.convert("RGB")

    return result, output_image, pattern_bundle


def build_report_sentence(result: dict[str, Any]) -> str:
    if result.get("status") != "성공":
        return f"분석 실패: {result.get('reason', result.get('status'))}"

    parts: list[str] = []
    completeness = result.get("completeness", {})
    glaze = result.get("glaze", {})
    era = result.get("era", {})

    if completeness.get("prediction") not in (None, "모델 없음"):
        score = completeness.get("score")
        suffix = f" (모델 점수 {score:.0%})" if isinstance(score, (int, float)) else ""
        parts.append(f"형태: 사진상 외형 기준 {completeness['prediction']} 추정{suffix}")

    if glaze.get("prediction") not in (None, "모델 없음"):
        score = glaze.get("score")
        suffix = f" (모델 점수 {score:.0%})" if isinstance(score, (int, float)) else ""
        parts.append(f"표면 광택 수준: {glaze['prediction']}{suffix}")

    if era.get("prediction") not in (None, "모델 없음", "예측 실패"):
        score = era.get("score")
        suffix = f" (모델 점수 {score:.0%}, 형태·양식 기반 참고 결과)" if isinstance(score, (int, float)) else ""
        parts.append(f"시대 후보: {era['prediction']}{suffix}")

    pattern_result = result.get("pattern_era_color")
    if pattern_result is None:
        parts.append("문양: 분석 안 함")
    elif "error" in pattern_result:
        parts.append(f"문양: VLM 분석 실패 ({pattern_result['error']})")
    else:
        min_agreement = pattern_result.get("min_agreement_used", FALLBACK_MIN_PATTERN_AGREEMENT)
        confirmed = [
            pattern
            for pattern in pattern_result.get("patterns", [])
            if (pattern.get("agreement_count") or 0) >= min_agreement
            and pattern.get("decision") != "판정보류"
        ]
        if confirmed:
            def _flags(pattern: dict[str, Any]) -> str:
                flags = ""
                if not pattern.get("name_in_reference_list", True):
                    flags += "[목록 외 명칭]"
                return flags

            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for pattern in confirmed:
                name = pattern.get("display_name", pattern["pattern_name"])
                grouped[name].append(pattern)

            summary = ", ".join(
                f"{name}"
                + (f" {len(patterns)}건" if len(patterns) > 1 else "")
                + f"({patterns[0]['agreement_count']}/{patterns[0]['agreement_total']}회)"
                + _flags(patterns[0])
                for name, patterns in grouped.items()
            )
            parts.append(f"주요 문양 후보: {summary}")

            # 클로즈업 육안상태조사 결과 중 이상 후보가 관찰된 문양만 별도로 요약한다.
            # "특이사항 없음"/"판정불가"는 굳이 보고서에 나열할 필요가 없어 걸러낸다.
            # 어투도 "훼손 관찰"처럼 단정하지 않고 "이상 후보 관찰(사람 재검토 필요)"로
            # 표현해, 이 결과가 AI의 최종 판정이 아니라 사람이 재확인할 후보 목록임을
            # 보고서에서도 일관되게 전달한다.
            notable_conditions = [
                pattern
                for pattern in confirmed
                if pattern.get("condition", {}).get("condition_status")
                in {"경미한 이상 의심", "이상 후보 관찰", "뚜렷한 이상 의심"}
            ]
            if notable_conditions:
                condition_summary = ", ".join(
                    f"{pattern.get('badge', pattern.get('display_name', pattern['pattern_name']))}"
                    f"({pattern['condition']['condition_status']})"
                    for pattern in notable_conditions
                )
                parts.append(
                    f"클로즈업 상태조사에서 이상 후보 관찰(사람 재검토 필요): {condition_summary}"
                )
        else:
            parts.append(f"문양: {min_agreement}회 이상 합의되고 판정보류가 아닌 후보 없음")

        held_patterns = [
            pattern
            for pattern in pattern_result.get("patterns", [])
            if pattern.get("decision") == "판정보류"
        ]
        if held_patterns:
            # 개수만 알려주면 실제로 유물의 어느 부분에 무엇이 있는지 보고서만
            # 보고는 알 수 없다. 예를 들어 어깨 부분에 명확히 보이는 띠 장식이
            # 판정보류로 빠졌는데 "1건 판정보류"라고만 하면 조사자가 그 부분에
            # 아무 장식도 없다고 오해할 수 있다. 위치(badge)와 후보 명칭을
            # 함께 적어 기본 화면에는 안 보여도 상세 JSON을 찾아보게 유도한다.
            held_summary = ", ".join(
                f"{pattern.get('badge', '?')}({pattern.get('display_name', pattern.get('pattern_name', '?'))} 추정, 근거 부족)"
                for pattern in held_patterns
            )
            parts.append(
                f"참고: {held_summary} - 판별 기준(필수 특징)을 충분히 확인하지 못해 "
                "판정보류 상태이며 기본 화면에는 표시하지 않음(상세 JSON 참고). "
                "판정보류라고 해서 그 위치에 문양이 없다는 뜻은 아니며, 명칭 확정이 "
                "어렵다는 뜻이므로 육안 재확인이 필요함"
            )

        calls_succeeded = pattern_result.get("calls_succeeded")
        calls_requested = pattern_result.get("calls_requested")
        if calls_succeeded is not None and calls_requested and calls_succeeded < calls_requested:
            parts.append(f"(VLM 호출 {calls_succeeded}/{calls_requested}회만 성공)")
        if calls_requested == 1:
            parts.append("주의: 반복 호출 없이 1회 결과만 사용됨 - 합의 검증이 이뤄지지 않음")

    return " / ".join(parts)


def _confirmed_patterns(result: dict[str, Any]) -> list[dict[str, Any]]:
    """기본 화면에 노출되는(판정보류 아니고 합의 기준을 넘긴) 문양만 골라낸다.
    build_report_sentence/build_inspection_text/needs_human_review이 같은
    기준을 반복해서 쓰므로 한 곳으로 모았다."""
    pattern_result = result.get("pattern_era_color") or {}
    min_agreement = pattern_result.get("min_agreement_used", FALLBACK_MIN_PATTERN_AGREEMENT)
    return [
        pattern
        for pattern in pattern_result.get("patterns", [])
        if (pattern.get("agreement_count") or 0) >= min_agreement
        and pattern.get("decision") != "판정보류"
    ]


_NOTABLE_CONDITION_STATUSES = {"경미한 이상 의심", "이상 후보 관찰", "뚜렷한 이상 의심"}


def needs_human_review(result: dict[str, Any]) -> bool:
    """이 조사 건을 전문가가 우선적으로 검토해야 하는지 여부(불리언 신호).

    AI 쪽에서 최종 승인/거부를 내리지 않는다 - 그건 전문가 검토·승인 몫이다.
    여기서는 "검토 대기열에서 이 건을 얼마나 먼저 볼지" 정도의 우선순위
    신호만 제공한다. 다음 중 하나라도 있으면 True:
    - 클로즈업 상태조사에서 이상 후보(경미/후보/뚜렷 중 하나)가 관찰된 문양이 있음
    - 상태조사 결과 자체에 human_review_required=True로 표시된 문양이 있음
    - 근거 부족으로 판정보류된 문양이 있음(명칭을 확정하지 못함)
    - 문양 분석(VLM)이 아예 실패했거나, 반복 호출 없이(n_calls=1) 단일 결과만
      써서 합의 검증이 이뤄지지 않음
    """
    if result.get("status") != "성공":
        return True

    pattern_result = result.get("pattern_era_color")
    if pattern_result is None:
        return False
    if "error" in pattern_result:
        return True

    confirmed = _confirmed_patterns(result)
    for pattern in confirmed:
        condition = pattern.get("condition") or {}
        if condition.get("condition_status") in _NOTABLE_CONDITION_STATUSES:
            return True
        if condition.get("human_review_required"):
            return True

    held_patterns = [
        pattern
        for pattern in pattern_result.get("patterns", [])
        if pattern.get("decision") == "판정보류"
    ]
    if held_patterns:
        return True

    calls_succeeded = pattern_result.get("calls_succeeded")
    calls_requested = pattern_result.get("calls_requested")
    if calls_succeeded is not None and calls_requested and calls_succeeded < calls_requested:
        return True
    if calls_requested == 1:
        return True

    return False


def build_inspection_text(result: dict[str, Any]) -> str:
    """"육안 Text" - 조사보고서 페이지(분석서 Page)에 그대로 얹을 수 있는
    자연어 서술형 텍스트를 만든다.

    build_report_sentence()는 Gradio 화면의 한 줄 기술 요약(슬래시로 이어붙인
    필드값 나열)이라 개발자가 보기엔 편해도 최종 사용자용 보고서 문장으로
    쓰기엔 딱딱하다. 이 함수는 같은 원본 데이터를 유물 외형/주요 문양/상태
    이상 후보/종합 의견 네 문단으로 풀어 쓴다. 내용은 항상 기존 필드
    (completeness/glaze/era/pattern_era_color)에서만 가져오고 새로 지어내지
    않는다 - 그래야 이 텍스트도 나머지 JSON과 마찬가지로 근거를 추적할 수 있다.
    """
    if result.get("status") != "성공":
        return f"분석에 실패하여 육안조사 결과를 생성하지 못했습니다. ({result.get('reason', result.get('status'))})"

    paragraphs: list[str] = []
    completeness = result.get("completeness", {})
    glaze = result.get("glaze", {})
    era = result.get("era", {})

    # 1) 유물 외형
    shape_sentences: list[str] = []
    if completeness.get("prediction") not in (None, "모델 없음"):
        shape_sentences.append(
            f"사진상 외형은 {completeness['prediction']}한 것으로 추정된다."
        )
    if completeness.get("limitation"):
        shape_sentences.append(completeness["limitation"] + ".")
    if glaze.get("prediction") not in (None, "모델 없음"):
        shape_sentences.append(f"표면 광택 수준은 {glaze['prediction']}으로 관찰된다.")
    if era.get("prediction") not in (None, "모델 없음", "예측 실패"):
        score = era.get("score")
        score_text = f" (모델 점수 {score:.0%})" if isinstance(score, (int, float)) else ""
        shape_sentences.append(
            f"시대는 형태·양식 기반 참고 결과로 {era['prediction']} 후보로 추정된다{score_text}."
        )
        evidence = era.get("evidence") or {}
        supporting = evidence.get("supporting_evidence") or []
        conflicting = evidence.get("conflicting_evidence") or []
        if supporting:
            shape_sentences.append("근거: " + ", ".join(supporting) + ".")
        if conflicting:
            shape_sentences.append(
                "다만 " + ", ".join(conflicting) + " 점은 이 시대 판단과는 다소 어긋나 보인다."
            )
    if shape_sentences:
        paragraphs.append("[유물 외형]\n" + " ".join(shape_sentences))

    pattern_result = result.get("pattern_era_color")
    if pattern_result is None:
        paragraphs.append("[주요 문양]\n문양 분석을 수행하지 않았다.")
    elif "error" in pattern_result:
        paragraphs.append(f"[주요 문양]\n문양 분석(VLM) 중 오류가 발생해 결과를 얻지 못했다. ({pattern_result['error']})")
    else:
        confirmed = _confirmed_patterns(result)
        # 2) 주요 문양
        if confirmed:
            unique_names: list[str] = []
            for pattern in confirmed:
                name = pattern.get("display_name", pattern.get("pattern_name", "?"))
                if name not in unique_names:
                    unique_names.append(name)
            names = ", ".join(unique_names)
            paragraphs.append(
                f"[주요 문양]\n표면에서 {names}이(가) 확인된다. "
                "문양명은 여러 차례 반복 분석에서 위치가 일치한 후보를 기준으로 하며, "
                "반복해서 같은 답이 나왔다는 뜻이지 실제로 맞다는 보장은 아니므로 "
                "명칭 확정은 조사자 검토가 필요하다."
            )
        else:
            paragraphs.append("[주요 문양]\n합의 기준을 넘기고 판정보류가 아닌 문양 후보가 없다.")

        held_patterns = [
            pattern
            for pattern in pattern_result.get("patterns", [])
            if pattern.get("decision") == "판정보류"
        ]
        if held_patterns:
            held_names = ", ".join(
                f"{pattern.get('badge', '?')}({pattern.get('display_name', pattern.get('pattern_name', '?'))} 추정)"
                for pattern in held_patterns
            )
            paragraphs.append(
                f"[참고] 이 외에 {held_names} 위치에서도 문양으로 보이는 부분이 있으나, "
                "판별 기준(필수 특징)을 충분히 확인하지 못해 명칭을 확정하지 못했다. "
                "문양이 없다는 뜻이 아니라 육안 재확인이 필요하다는 뜻이다."
            )

        # 3) 상태 이상 후보
        notable_conditions = [
            pattern
            for pattern in confirmed
            if pattern.get("condition", {}).get("condition_status") in _NOTABLE_CONDITION_STATUSES
        ]
        if notable_conditions:
            issue_sentences = []
            for pattern in notable_conditions:
                condition = pattern.get("condition", {})
                badge = pattern.get("badge", pattern.get("display_name", pattern.get("pattern_name", "?")))
                issue_types = ", ".join(
                    issue.get("issue_type", "?") for issue in condition.get("issues", [])
                ) or "특이 소견"
                issue_sentences.append(
                    f"{badge}에서는 {condition.get('condition_status')} 수준으로 "
                    f"{issue_types}이(가) 관찰된다."
                )
            paragraphs.append(
                "[상태 이상 후보]\n" + " ".join(issue_sentences) + " "
                "이 소견은 클로즈업 사진 한 장을 기준으로 한 AI의 1차 판단이며, "
                "촬영 화질·반사광 등으로 인한 오판일 가능성도 함께 고려해 "
                "전문가가 재확인해야 한다."
            )
        else:
            paragraphs.append(
                "[상태 이상 후보]\n클로즈업 상태조사 결과, 뚜렷한 이상 후보는 관찰되지 않았다. "
                "다만 이는 사진상으로 확인되지 않았다는 뜻이며, 실물 확인을 대체하지 않는다."
            )

        # 4) 종합 의견
        review_flag = needs_human_review(result)
        if review_flag:
            paragraphs.append(
                "[종합 의견]\n뚜렷한 균열·박락 등 확정적 손상 근거는 확인되지 않았으나, "
                "명칭 미확정 문양 또는 상태 이상 후보가 있어 전문가 재검토를 권고한다."
            )
        else:
            paragraphs.append(
                "[종합 의견]\n현재까지 확인된 범위에서는 전문가 우선 검토가 필요한 "
                "특이 소견이 발견되지 않았다. 다만 이는 AI의 1차 분석 결과이며 "
                "최종 판단은 전문가 검토를 거쳐야 한다."
            )

    return "\n\n".join(paragraphs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--no-vlm-pattern", action="store_true")
    parser.add_argument("--out-image", default=None)
    parser.add_argument("--n-calls", type=int, default=3)
    arguments = parser.parse_args()

    analysis, result_image, _bundle = analyze_pottery(
        arguments.image,
        use_vlm_pattern=not arguments.no_vlm_pattern,
        n_calls=arguments.n_calls,
    )
    print(json.dumps(analysis, ensure_ascii=False, indent=2))
    print("\n[요약]", build_report_sentence(analysis))

    if arguments.out_image and result_image is not None:
        result_image.save(arguments.out_image)
        print(f"\n결과 이미지 저장: {arguments.out_image}")