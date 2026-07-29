"""
X-ray AI 서비스 설정

환경변수로 덮어쓸 수 있다. Docker 실행 시 -e 옵션 또는
docker-compose environment 항목으로 주입한다.


[여러 명이 작업할 때의 규칙]

이 파일은 결함 탐지와 조각 결합 양쪽에서 참조한다.
git 충돌을 줄이기 위해 섹션을 나눠 쓴다.

- 공통 섹션은 합의 후 수정한다
- 각 파트는 자기 섹션에만 값을 추가한다
- 다른 파트 섹션은 건드리지 않는다

환경변수 접두사도 구분한다.

    XRAY_*          공통
    XRAY_ANOMALY_*  결함 탐지 전용 (필요 시)
    XRAY_STITCH_*   조각 결합 전용
"""

import os
from pathlib import Path


# ------------------------------------------------------------
# 모델
# ------------------------------------------------------------

MODEL_PATH = Path(
    os.getenv(
        "XRAY_MODEL_PATH",
        "/code/models/final_xray_defect_best.pt"
    )
)

# 학습 시 imgsz=1280 이었으므로 추론도 동일 계열로 맞춘다.
#
# 결합 완료본은 원본이 5000px 이상으로 크기 때문에
# 2048을 사용해야 이상영역이 축소되지 않는다.
# 실측: 결합본 640=11건, 1280=20건, 2048=24건

IMGSZ_ASSEMBLED = int(
    os.getenv("XRAY_IMGSZ_ASSEMBLED", "2048")
)

IMGSZ_FRAGMENT = int(
    os.getenv("XRAY_IMGSZ_FRAGMENT", "1280")
)

# 후보 제시가 목적이므로 낮게 설정한다.
# 오탐은 전문가 검수 단계에서 제외한다.

DEFAULT_CONF = float(
    os.getenv("XRAY_DEFAULT_CONF", "0.08")
)

# 한 이미지에서 반환할 최대 영역 수
MAX_DETECTIONS = int(
    os.getenv("XRAY_MAX_DETECTIONS", "300")
)

# NMS IoU 임계값
#
# Ultralytics 기본값 0.7에서는 같은 특징이 두 번 탐지되는
# 중복이 발생했다. 실측으로 0.5를 적용해 결합본 10건 → 6건으로
# 줄였으며, 고유 영역은 유지되었다.

NMS_IOU = float(os.getenv("XRAY_NMS_IOU", "0.5"))


# ------------------------------------------------------------
# 실행 환경
# ------------------------------------------------------------

# "cpu" 또는 "0" (GPU 인덱스)
DEVICE = os.getenv("XRAY_DEVICE", "cpu")

# 업로드 임시 저장 경로
UPLOAD_DIR = Path(
    os.getenv("XRAY_UPLOAD_DIR", "/tmp/xray_uploads")
)

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 허용 이미지 확장자
ALLOWED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png",
    ".bmp", ".tif", ".tiff",
}

# 업로드 파일 크기 상한 (바이트)
MAX_UPLOAD_SIZE = int(
    os.getenv(
        "XRAY_MAX_UPLOAD_SIZE",
        str(50 * 1024 * 1024)
    )
)


# ------------------------------------------------------------
# 모델 메타 (응답에 포함하여 추적성 확보)
# ------------------------------------------------------------

MODEL_INFO = {
    "task": "detection",
    "architecture": "yolo11s",
    "class_names": {0: "anomaly"},
    "train_imgsz": 1280,
    "val_metrics": {
        "mAP50": 0.366,
        "mAP50_95": 0.118,
        "precision": 0.480,
        "recall": 0.385,
    },
    "note": (
        "본 모델은 균열·부식·결손을 판정하지 않으며 "
        "검토 필요 영역 후보를 제시한다. "
        "최종 판정은 보존처리 전문가가 수행해야 한다."
    ),
}


# ------------------------------------------------------------
# CORS 허용 출처
#
# 개발 중 React(Vite)에서 직접 호출할 때 필요하다.
# 쉼표로 구분한다.
# ------------------------------------------------------------

CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "XRAY_CORS_ORIGINS",
        "http://localhost:5173,http://localhost:3000"
    ).split(",")
    if origin.strip()
]


# ------------------------------------------------------------
# OpenAI 상태조사 문안 생성
#
# 키가 없으면 /report 엔드포인트만 비활성화되고
# 탐지 기능은 정상 동작한다.
# ------------------------------------------------------------

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6")

# 출력 토큰 상한 (스타일 미지정 시 기본값)
#
# 8000에서는 상세본 9절 마지막 문장이 잘렸다.
# 10000으로 올려 완결을 확인했다.
LLM_MAX_OUTPUT_TOKENS = int(
    os.getenv("XRAY_LLM_MAX_TOKENS", "10000")
)

# 개별 서술 대상 건수
#
# 전체 영역을 서술하면 토큰을 초과한다.
# 나머지는 통계로 요약해 전달한다.
#
# 프롬프트에서 "최대 N개만 작성"이라고 지시하면서
# 실제로는 그보다 많이 전달하면, 어느 것을 고를지가
# 모델 판단에 맡겨져 결과가 흔들린다.
# 보낼 건수와 지시 건수를 일치시킨다.

# summary: PPT 삽입용 요약본, 1500자 내외
LLM_SUMMARY_TOP_ASSEMBLED = int(
    os.getenv("XRAY_LLM_SUMMARY_TOP_ASSEMBLED", "5")
)

LLM_SUMMARY_TOP_FRAGMENT = int(
    os.getenv("XRAY_LLM_SUMMARY_TOP_FRAGMENT", "3")
)

LLM_SUMMARY_MAX_TOKENS = int(
    os.getenv("XRAY_LLM_SUMMARY_MAX_TOKENS", "4000")
)

# detailed: 공식 기록용 상세본, 9개 절
LLM_DETAILED_TOP_ASSEMBLED = int(
    os.getenv("XRAY_LLM_DETAILED_TOP_ASSEMBLED", "12")
)

LLM_DETAILED_TOP_FRAGMENT = int(
    os.getenv("XRAY_LLM_DETAILED_TOP_FRAGMENT", "10")
)

LLM_DETAILED_MAX_TOKENS = int(
    os.getenv("XRAY_LLM_DETAILED_MAX_TOKENS", "10000")
)

# API 요청 크기 제한을 위한 이미지 축소 기준
LLM_IMAGE_MAX_SIDE = int(
    os.getenv("XRAY_LLM_IMAGE_MAX_SIDE", "1800")
)

# 조각·컬러 이미지 최대 전송 장수
LLM_MAX_IMAGES = int(
    os.getenv("XRAY_LLM_MAX_IMAGES", "6")
)


# ============================================================
# 조각 결합 파트 설정
#
# 담당자가 이 섹션에 추가한다.
# 결함 탐지 파트는 이 아래를 수정하지 않는다.
#
# 환경변수는 XRAY_STITCH_ 접두사를 쓴다.
# ============================================================

# 결합 설정 JSON 경로
STITCH_CONFIG_DIR = Path(
    os.getenv(
        "XRAY_STITCH_CONFIG_DIR",
        "/code/configs",
    )
)

# 매핑 JSON 경로
STITCH_MAPPING_DIR = Path(
    os.getenv(
        "XRAY_STITCH_MAPPING_DIR",
        "/code/mappings",
    )
)

# 결합 엔진(batch_assemble.py) 위치
#
# 엔진은 원래 별도 배치 도구다. Dockerfile에서
# engine/ 을 이미지 안으로 복사해 여기에 둔다.
STITCH_ENGINE_DIR = Path(
    os.getenv(
        "XRAY_STITCH_ENGINE_DIR",
        "/code/engine",
    )
)

# 엔진 진입점
#
# 엔진에는 진입점이 둘 있다.
#
#   assemble_xray.py   컬러 기준 이미지 1장 + 조각 폴더
#                      업로드 기반 서비스는 이쪽을 쓴다
#
#   batch_assemble.py  사전 등록된 데이터셋 일괄 처리
#                      dataset_manifest.json 과 매핑 파일이
#                      있어야 하고 유물 ID가 등록되어 있어야 한다
#
# 사용자가 올린 임의의 조각을 처리해야 하므로
# assemble_xray.py 를 기본값으로 둔다.
STITCH_ENGINE_SCRIPT = os.getenv(
    "XRAY_STITCH_ENGINE_SCRIPT",
    "assemble_xray.py",
)

# 매핑 파일명
#
# batch_assemble.py 를 쓸 때만 필요하다. 지금은
# assemble_xray.py 를 쓰므로 사용하지 않는다.
# 일괄 처리 모드를 도입하면 다시 쓰인다.
STITCH_MAPPING_NAME = os.getenv(
    "XRAY_STITCH_MAPPING_NAME",
    "mapping.color_front.json",
)

# 기본 결합 설정 이름
#
# 엔진의 --config 로 전달된다. 요청에서 config_name을
# 지정하면 그 값이 우선한다.
#
# 실제 파일명은
# config.batch_fast.color_slot_voronoi_all_fragments_v13_conservative.json
# 처럼 길다. 짧은 별칭으로 적어도 이름을 포함하는 파일을
# 찾도록 되어 있다.
STITCH_DEFAULT_CONFIG_NAME = os.getenv(
    "XRAY_STITCH_CONFIG_NAME",
    "v13_conservative",
)

# 결합 제한 시간 (초)
#
# 엔진이 매칭에 실패하면 무한정 도는 경우가 있어
# 반드시 상한을 둔다. 조각 30장 기준 수 분이 걸리므로
# 결함 탐지(300초)보다 넉넉하게 잡는다.
#
# Spring의 xray.ai.stitch-timeout-seconds 는 이 값보다
# 커야 한다. 반대면 엔진이 도는 중에 Spring이 먼저
# 끊어져 원인 파악이 어려워진다.
STITCH_TIMEOUT_SECONDS = int(
    os.getenv("XRAY_STITCH_TIMEOUT", "1800")
)

# 반환 이미지 긴 변 상한 (0이면 원본 그대로)
#
# 결합본은 5000px 이상이라 base64가 수십 MB가 된다.
# 전송이 부담되면 이 값을 지정해 축소해 반환한다.
# 좌표는 항상 원본 기준이며 previewScale로 환산한다.
STITCH_RETURN_MAX_SIDE = int(
    os.getenv("XRAY_STITCH_RETURN_MAX_SIDE", "0")
)
