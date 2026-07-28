"""
X-ray AI 서비스

Spring Boot에서 HTTP로 호출하는 AI 추론 전용 서버.
학습 기능은 포함하지 않는다.

실행
    uvicorn app.main:app --host 0.0.0.0 --port 8000


[이 파일의 역할]

앱 초기화와 라우터 등록만 담당한다.
기능별 엔드포인트는 routers/ 아래에 둔다.

이렇게 나눈 이유는 여러 명이 작업할 때
같은 파일을 동시에 수정해 git 충돌이 나는 것을
막기 위함이다. 각자 자기 라우터 파일만 만지면 된다.

경로 접두사가 필요해지면 아래 include_router에서
prefix만 지정하면 되고, 라우터 파일은 건드리지 않는다.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.routers import anomaly, stitch
from app.services import detector


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    앱 시작 시 모델을 1회 로드한다.

    요청마다 로드하면 매번 수 초가 소요되므로
    반드시 여기서 미리 올려둔다.
    """
    print("모델 로드 시작:", config.MODEL_PATH)

    detector.load_model()

    print("모델 로드 완료, device =", config.DEVICE)

    yield

    print("서버 종료")


app = FastAPI(
    title="X-ray AI 서비스",
    version="1.1.0",
    lifespan=lifespan,
)


# ------------------------------------------------------------
# CORS
#
# 브라우저에서 직접 호출하려면 반드시 필요하다.
# 없으면 React 개발 서버에서 요청이 차단된다.
#
# 운영에서는 Spring을 거치므로 브라우저가 직접
# 이 서버를 호출하지 않는다. 그때는 허용 목록을 비우거나
# 내부 도메인만 남긴다.
# ------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------
# 라우터 등록
#
# prefix를 지정하면 해당 라우터의 모든 경로 앞에 붙는다.
# 두 파트의 경로가 겹치면 나중에 등록된 쪽이
# 앞의 것을 덮어쓰므로 주의한다.
#
# 이상영역 탐지는 기존 Spring 연동 경로를 유지한다.
# 결합 Job API만 /api 접두사를 사용한다.
#
# 결과 경로:
#   POST /api/stitch/jobs
#   GET  /api/jobs/{jobId}
# ------------------------------------------------------------

app.include_router(anomaly.router)
app.include_router(stitch.router, prefix="/api")


# ------------------------------------------------------------
# 서비스 공통
#
# Docker healthcheck와 Spring의 상태 확인에 쓴다.
# ------------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "modelLoaded": detector._model is not None,
        "device": config.DEVICE,
        "llmEnabled": bool(config.OPENAI_API_KEY),
        "stitchReady": (
            config.STITCH_BATCH_SCRIPT.is_file()
            and config.STITCH_MAPPING_PATH.is_file()
            and config.STITCH_CONFIG_DIR.is_dir()
        ),
        "stitchJobsRoot": str(config.STITCH_JOBS_ROOT),
    }


@app.get("/model-info")
def model_info():
    """모델 메타 정보. 보고서에 기록할 추적 정보."""
    return config.MODEL_INFO
