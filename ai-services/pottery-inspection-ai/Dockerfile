# 육안조사 AI 모듈 (app/pottery_api.py) 도커 이미지
#
# 빌드는 반드시 저장소 루트에서 실행:
#   docker build -t pottery-inspection-api .
#
# 자세한 실행 방법(볼륨/환경변수 등)은 docs/DOCKER.md 참고.

FROM python:3.11-slim

# opencv-python-headless / torch 런타임에 필요한 최소 시스템 라이브러리 +
# SAM2를 git 저장소에서 직접 설치하기 위한 git.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# 의존성을 먼저 설치해 코드만 바뀔 때 캐시를 재사용한다(빌드 속도).
COPY app/requirements.txt ./app/requirements.txt
RUN pip install --no-cache-dir -r app/requirements.txt

# 앱 코드 + 학습된 RF 모델(completeness_rf.joblib, glaze_rf_v2.joblib) 복사.
# ai_hub/(시대 CNN 모델)는 크기가 커서 이미지에 안 넣고 런타임에 볼륨으로 마운트한다.
COPY app/ ./app/

WORKDIR /srv/app

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "pottery_api:app", "--host", "0.0.0.0", "--port", "8000"]
