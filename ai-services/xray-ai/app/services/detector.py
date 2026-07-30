"""
X-ray 이상영역 탐지 서비스

Colab 노트북 셀 31의 analyze_xray_image 함수를
서버용으로 이관한 것이다.

핵심 차이점
- 모델을 앱 시작 시 1회만 로드한다 (요청마다 로드하면 매우 느림)
- 이미지 렌더링을 하지 않고 좌표만 반환한다
  (박스 그리기는 React 캔버스에서 수행)
- 학습 코드는 포함하지 않는다
"""

from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from ultralytics import YOLO

from app import config

# ------------------------------------------------------------
# 모델 싱글턴
#
# 모듈 레벨 변수에 담아 프로세스당 1개만 유지한다.
# ------------------------------------------------------------

_model: Optional[YOLO] = None


def load_model() -> YOLO:
    """
    모델을 로드한다. 이미 로드되어 있으면 기존 인스턴스를 반환한다.
    앱 시작 시 lifespan에서 한 번 호출한다.
    """
    global _model

    if _model is not None:
        return _model

    if not config.MODEL_PATH.exists():
        raise FileNotFoundError(f"모델 파일이 없습니다: {config.MODEL_PATH}")

    _model = YOLO(str(config.MODEL_PATH))

    # 첫 추론이 느리므로 더미 입력으로 워밍업한다.
    dummy = np.zeros((640, 640, 3), dtype=np.uint8)

    _model.predict(
        source=dummy,
        imgsz=640,
        device=config.DEVICE,
        verbose=False,
    )

    return _model


def get_model() -> YOLO:
    if _model is None:
        return load_model()
    return _model


# ------------------------------------------------------------
# 이미지 읽기
# ------------------------------------------------------------


def read_image_bgr(image_path) -> Optional[np.ndarray]:
    """
    한글·특수문자 파일명에서도 안전하게 이미지를 읽는다.
    cv2.imread는 Windows ANSI 인코딩 문제가 있어 사용하지 않는다.
    """
    try:
        binary = np.fromfile(str(image_path), dtype=np.uint8)

        return cv2.imdecode(binary, cv2.IMREAD_COLOR)

    except Exception:
        return None


# ------------------------------------------------------------
# 위치 계산
# ------------------------------------------------------------


def get_position_name(
    center_x: float,
    center_y: float,
    width: int,
    height: int,
) -> str:
    """중심 좌표를 3x3 위치 명칭으로 변환한다."""

    x_ratio = center_x / max(width, 1)
    y_ratio = center_y / max(height, 1)

    if x_ratio < 1 / 3:
        horizontal = "좌측"
    elif x_ratio < 2 / 3:
        horizontal = "중앙"
    else:
        horizontal = "우측"

    if y_ratio < 1 / 3:
        vertical = "상단"
    elif y_ratio < 2 / 3:
        vertical = "중앙"
    else:
        vertical = "하단"

    if horizontal == "중앙" and vertical == "중앙":
        return "중앙"

    return f"{horizontal} {vertical}"


# ------------------------------------------------------------
# 추론
# ------------------------------------------------------------


def detect_anomalies(
    image_path: Path,
    analysis_target: str = "원본 조각",
    confidence: Optional[float] = None,
    imgsz: Optional[int] = None,
    start_index: int = 1,
) -> dict:
    """
    X-ray 이미지 한 장에서 이상영역을 탐지한다.

    Parameters
    ----------
    image_path : 이미지 경로
    analysis_target : "결합 완료본" 또는 "원본 조각"
    confidence : 신뢰도 임계값. None이면 기본값 사용
    imgsz : 추론 해상도. None이면 대상별 기본값 사용
    start_index : 영역 ID 시작 번호

    Returns
    -------
    dict
        regions : 탐지 영역 목록
        summary : 이미지 요약 정보
        next_index : 다음 영역 ID 번호
    """
    model = get_model()

    image_bgr = read_image_bgr(image_path)

    if image_bgr is None:
        raise ValueError(f"이미지를 읽을 수 없습니다: {image_path.name}")

    height, width = image_bgr.shape[:2]

    if confidence is None:
        confidence = config.DEFAULT_CONF

    if imgsz is None:
        imgsz = (
            config.IMGSZ_ASSEMBLED if analysis_target == "결합 완료본" else config.IMGSZ_FRAGMENT
        )

    prediction = model.predict(
        source=image_bgr,
        conf=float(confidence),
        imgsz=int(imgsz),
        iou=config.NMS_IOU,
        device=config.DEVICE,
        max_det=config.MAX_DETECTIONS,
        verbose=False,
    )[0]

    regions = []
    index = start_index

    has_boxes = prediction.boxes is not None and len(prediction.boxes) > 0

    if has_boxes:
        boxes_xyxy = prediction.boxes.xyxy.cpu().numpy()
        confidences = prediction.boxes.conf.cpu().numpy()

        # 신뢰도 내림차순
        order = np.argsort(-confidences)

        for i in order:
            x1, y1, x2, y2 = boxes_xyxy[i]
            conf = float(confidences[i])

            center_x = float((x1 + x2) / 2)
            center_y = float((y1 + y2) / 2)

            box_area = max(float((x2 - x1) * (y2 - y1)), 0.0)

            area_ratio = box_area / max(width * height, 1)

            regions.append(
                {
                    "regionId": f"R-{index:03d}",
                    "analysisTarget": analysis_target,
                    "fileName": image_path.name,
                    "className": "anomaly",
                    "confidence": round(conf, 4),
                    "position": get_position_name(center_x, center_y, width, height),
                    "areaRatioPercent": round(area_ratio * 100, 3),
                    "bbox": {
                        "x1": round(float(x1), 2),
                        "y1": round(float(y1), 2),
                        "x2": round(float(x2), 2),
                        "y2": round(float(y2), 2),
                    },
                    "center": {
                        "x": round(center_x, 2),
                        "y": round(center_y, 2),
                    },
                }
            )

            index += 1

    summary = {
        "fileName": image_path.name,
        "analysisTarget": analysis_target,
        "imageWidth": int(width),
        "imageHeight": int(height),
        "inferenceImgsz": int(imgsz),
        "confidenceThreshold": float(confidence),
        "regionCount": len(regions),
    }

    return {
        "regions": regions,
        "summary": summary,
        "nextIndex": index,
    }
