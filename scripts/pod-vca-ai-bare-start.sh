#!/usr/bin/env bash
# RunPod GPU 팟 위에서 vca-ai의 FastAPI 어댑터(ai-services/vca-ai/app)를
# Docker 없이 bare uvicorn 프로세스로 띄운다. 팟 자체가 권한 없는(unprivileged)
# 컨테이너라 Docker-in-Docker(docker-compose)를 못 쓰기 때문에 필요한 경로다
# (`capsh --print`로 cap_sys_admin 없음, `docker` 명령 자체가 없음을 확인함).
#
# 전제: 이 스크립트를 실행하기 전에
#   1) ai-services/vca-ai/app/ 이 팟의 $APP_DIR 밑에 이미 복사돼 있어야 한다
#      (scp -r ai-services/vca-ai/app root@<pod>:$APP_DIR/app 등)
#   2) $ENGINE_ROOT($VCA_ENGINE_ROOT)에 엔진 코드(modules/)와 uv 프로젝트
#      (pyproject.toml/uv.lock)가 이미 준비돼 있고, $UV_PROJECT_ENVIRONMENT에
#      uv sync까지 끝나 있어야 한다(이번 세션에서 검증한 "venv/output 전부
#      팟 로컬 디스크" 구성 - /root/local-run 같은 경로).
#
# 사용법 (팟 안에서):
#   APP_DIR=/root/local-run \
#   VCA_ENGINE_ROOT=/root/local-run \
#   UV_PROJECT_ENVIRONMENT=/root/vca-uv-env-local \
#   VCA_MODEL_CACHE_ROOT=/workspace/models \
#   VCA_DOCUMENT_CORPUS_DIR=/workspace/vca-document-corpus \
#     bash scripts/pod-vca-ai-bare-start.sh
set -euo pipefail

export PATH="/root/.local/bin:$PATH"

APP_DIR="${APP_DIR:?APP_DIR is required (directory containing app/, synced from ai-services/vca-ai/app)}"
export VCA_ENGINE_ROOT="${VCA_ENGINE_ROOT:?VCA_ENGINE_ROOT is required}"
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:?UV_PROJECT_ENVIRONMENT is required}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/workspace/vca-uv-cache}"
export VCA_MODEL_CACHE_ROOT="${VCA_MODEL_CACHE_ROOT:?VCA_MODEL_CACHE_ROOT is required}"
export VCA_DOCUMENT_CORPUS_DIR="${VCA_DOCUMENT_CORPUS_DIR:?VCA_DOCUMENT_CORPUS_DIR is required}"
export VCA_SHARED_STORAGE_ROOT="${VCA_SHARED_STORAGE_ROOT:-$APP_DIR/shared}"
export VCA_RUN_MODE="${VCA_RUN_MODE:-real}"
export VCA_DEVICE="${VCA_DEVICE:-cuda}"
export VCA_DEBUG_TIMING="${VCA_DEBUG_TIMING:-1}"
export VCA_FAULTHANDLER="${VCA_FAULTHANDLER:-1}"
# 기본값(120초)은 dry-run용이라 실제 GPU 파이프라인(수십 분)에는 턱없이
# 부족해서 첫 실전 테스트가 "run timed out after 120 seconds"로 죽었다 -
# docker-compose.yml의 컨테이너 배포 기본값(3900초)과 맞춘다.
export VCA_RUN_TIMEOUT_SECONDS="${VCA_RUN_TIMEOUT_SECONDS:-3900}"
PORT="${PORT:-8000}"

mkdir -p "$VCA_SHARED_STORAGE_ROOT"

echo "== fastapi/uvicorn을 엔진 venv에 추가 설치 (이미 있으면 즉시 끝남) =="
uv pip install --python "$UV_PROJECT_ENVIRONMENT/bin/python" fastapi uvicorn[standard]

echo "== vca-ai FastAPI 어댑터를 0.0.0.0:$PORT 에 기동 =="
cd "$APP_DIR"
exec "$UV_PROJECT_ENVIRONMENT/bin/python" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
