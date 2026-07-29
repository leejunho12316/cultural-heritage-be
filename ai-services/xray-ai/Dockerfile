FROM python:3.12-slim

# libgl1 / libglib2.0-0 은 opencv 런타임 의존성이다.
# opencv-python-headless를 쓰므로 GUI 라이브러리는 넣지 않는다.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /code

RUN pip install --no-cache-dir \
        torch==2.5.1 torchvision==0.20.1 \
        --index-url https://download.pytorch.org/whl/cpu

COPY app/requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY models/ ./models/

# ------------------------------------------------------------
# 조각 결합 파트
#
# 결합 엔진은 별도 CLI 도구다. 서비스가 subprocess로
# 호출하므로 이미지 안에 함께 넣는다.
#
#   engine/    xray_assembler/ 패키지와 실행 스크립트 일체
#   configs/   결합 설정 JSON
#
# engine/ 내부 구조는 두 가지 모두 지원한다.
#
#   engine/                      engine/
#   ├── xray_assembler/          ├── xray_assembler/
#   └── scripts/                 ├── assemble_xray.py
#       └── assemble_xray.py     └── batch_assemble.py
#
# 매핑 파일은 batch_assemble.py 를 쓸 때만 필요하다.
# 지금은 assemble_xray.py 를 쓰므로 복사하지 않는다.
#
# 두 폴더는 xray-ai/ 아래에 두어야 빌드 컨텍스트에 들어온다.
# 엔진이 아직 없으면 이 두 줄을 주석 처리해도 결함 탐지는
# 정상 동작하며, 결합만 502를 반환한다.
# ------------------------------------------------------------

COPY engine/ ./engine/
COPY configs/ ./configs/

ENV XRAY_MODEL_PATH=/code/models/final_xray_defect_best.pt
ENV XRAY_DEVICE=cpu
ENV PYTHONUNBUFFERED=1

# 조각 결합 기본 경로
ENV XRAY_STITCH_ENGINE_DIR=/code/engine
ENV XRAY_STITCH_CONFIG_DIR=/code/configs
ENV XRAY_STITCH_ENGINE_SCRIPT=assemble_xray.py

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
