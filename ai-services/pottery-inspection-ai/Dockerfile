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

# sam2의 소스코드를 받아서 빌드할 때 그래픽카드(CUDA)용 확장까지 컴파일하려고 시도하는데, 이 컨테이너는 CPU 전용이라 그래픽카드가 없다.
# 그래픽카드가 없는데 그래픽카드를 쓰려는 이 시도를 그대로 두면 CPU 코어 수만큼 병렬 컴파일이 붙으면서 메모리를 다 써버려 빌드가 멈추거나 죽는다.
# SAM2_BUILD_CUDA=0으로 그 시도 자체를 꺼서 순수 파이썬만 설치한다.
ENV SAM2_BUILD_CUDA=0
ENV SAM2_BUILD_ALLOW_ERRORS=1

# PyPI 기본 torch는 그래픽카드 구동용 대용량 부속 파일(nvidia-*)까지 딸려와서 다운로드/압축해제 중 메모리를 크게 잡아먹는다(WSL 메모리를 꽉 채워 시스템
# 이 컨테이너는 그래픽카드가 없으므로 처음부터 CPU 전용 torch/torchvision을 받는다
# - app/models/grounded_sam_detector.py가 "torch.cuda.is_available()이면 cuda, 아니면 cpu"로 이미 자동 분기하므로 동작에는 차이가 없다.
ENV PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

# 의존성을 먼저 설치해 코드만 바뀔 때 캐시를 재사용한다(빌드 속도).
# torch/torchvision은 위에서 이미 설치했으므로 아래에서 재설치되지 않는다.
COPY app/requirements.txt ./app/requirements.txt
RUN pip install --no-cache-dir -r app/requirements.txt

# 앱 코드 + 학습된 RF 모델(completeness_rf.joblib, glaze_rf_v2.joblib) 복사.
COPY app/ ./app/

# 시대 판정 CNN 모델(43MB, git에 커밋되어 있음)도 이미지에 함께 굽는다.
# 예전엔 "용량이 커서" 런타임 볼륨 마운트로 뺐었지만, 실제로는 43MB 정도라
# torch/SAM2까지 들어간 이미지 크기에 비하면 무시할 만한 수준이라 COPY로 단순화했다.
COPY ai_hub/pottery_multitask_model_v2 ./ai_hub/pottery_multitask_model_v2

WORKDIR /srv/app

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "pottery_api:app", "--host", "0.0.0.0", "--port", "8000"]
