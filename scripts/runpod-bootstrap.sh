#!/usr/bin/env bash
# RunPod GPU 팟에서 이 저장소를 처음 띄울 때 한 번 실행하는 스크립트.
# 전제: 이 저장소가 이미 팟 안에 있고(git clone 또는 업로드), 현재 디렉터리가
# 저장소 루트다.
#
#   ssh root@<pod-id>-<ssh-port>.proxy.runpod.net -p <port>
#   git clone <repo-url> cultural-heritage-be && cd cultural-heritage-be
#   cp .env.runpod.example .env && nano .env   # TODO 표시된 값 채우기
#   bash scripts/runpod-bootstrap.sh
#
# vca-ai만 GPU를 쓴다. xray-ai/pottery-inspection-ai/conservation-guide-ai는
# CPU로 같이 뜨는데(가벼움), backend가 이 넷 모두를 depends_on: service_started로
# 걸어놔서 개별적으로 빼기가 번거롭기 때문이다(docker-compose.prod.yml 상단
# 주석에 같은 문제가 이미 기록돼 있음) - 그냥 다 같이 띄운다.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== 1/5 GPU passthrough 확인 =="
if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "nvidia-smi가 없다 - GPU가 없는 팟이거나 드라이버가 안 깔린 템플릿이다." >&2
  exit 1
fi
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

if ! docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi >/dev/null 2>&1; then
  echo "컨테이너에서 GPU를 못 본다 - NVIDIA Container Toolkit이 이 템플릿에 없을 수 있다." >&2
  echo "  (docker info | grep -i nvidia 로 nvidia runtime 등록 여부를 확인할 것)" >&2
  exit 1
fi
echo "GPU passthrough OK"

echo "== 2/5 필수 환경변수 확인 =="
if [ ! -f .env ]; then
  echo ".env가 없다. cp .env.runpod.example .env 하고 TODO 값을 채운 뒤 다시 실행할 것." >&2
  exit 1
fi
# shellcheck disable=SC1091
set -a; source .env; set +a
missing=()
for var in VCA_ACCESS_TOKEN POSTGRES_PASSWORD JWT_SECRET AWS_S3_PRESIGN_ENDPOINT APP_CORS_ALLOWED_ORIGINS; do
  value="${!var:-}"
  if [ -z "$value" ] || [[ "$value" == *CHANGE_ME* ]] || [[ "$value" == *REPLACE-WITH* ]]; then
    missing+=("$var")
  fi
done
if [ "${#missing[@]}" -gt 0 ]; then
  printf '.env에 아직 채워야 할 값이 있다: %s\n' "${missing[*]}" >&2
  exit 1
fi
echo "필수 값 채워짐 확인"

echo "== 3/5 shared 디렉터리 + 네트워크 볼륨 디렉터리 준비 =="
mkdir -p shared/vca/input-store shared/vca/engine-output shared/vca/document-corpus shared/jobs

RUNPOD_VOLUME_PATH="${RUNPOD_VOLUME_PATH:-/workspace}"
if [ ! -d "$RUNPOD_VOLUME_PATH" ]; then
  echo "RUNPOD_VOLUME_PATH=$RUNPOD_VOLUME_PATH 가 없다 - 팟 생성 시 네트워크 볼륨을" >&2
  echo "이 경로에 마운트했는지 확인할 것(안 하면 모델을 재기동마다 다시 받는다)." >&2
  exit 1
fi
# docker-compose.runpod.yml이 여기 하위 경로들로 bind mount한다 - Docker가
# bind mount 대상 디렉터리를 알아서 만들어주지 않는 경우가 있어 미리 만든다.
mkdir -p \
  "$RUNPOD_VOLUME_PATH/vca-uv-cache" \
  "$RUNPOD_VOLUME_PATH/vca-models" \
  "$RUNPOD_VOLUME_PATH/pottery-hf-cache" \
  "$RUNPOD_VOLUME_PATH/pgdata" \
  "$RUNPOD_VOLUME_PATH/minio-data"

# engine-output은 반대로 네트워크 볼륨이 아니라 팟 로컬 디스크에 둔다 - vca-ai
# 파이프라인 중간 산출물(스테이지별 마스크/크롭/레코드, 파일 수천 개)을 FUSE
# 네트워크 마운트 위에서 재귀 스캔하면 syscall 하나하나가 네트워크 왕복을
# 먹어 몇 분씩 걸리는 게 실측으로 확인됐다. 이 디렉터리는 런이 끝나면
# 버려도 되는 산출물이라 팟이 사라지면 같이 지워져도 무방하다.
RUNPOD_LOCAL_SCRATCH_PATH="${RUNPOD_LOCAL_SCRATCH_PATH:-/var/lib/vca-local}"
mkdir -p "$RUNPOD_LOCAL_SCRATCH_PATH/engine-output"

echo "== 4/5 빌드 + 기동 =="
docker compose --profile local \
  -f docker-compose.yml -f docker-compose.runpod.yml \
  up --build -d

echo "== 5/5 상태 =="
docker compose ps
cat <<'EOF'

첫 기동은 vca-ai가 Qwen2.5-VL/SAM2 등 모델을 Hugging Face에서 받느라
5~15분 걸릴 수 있다(VCA_BOOTSTRAP_MODELS=true). 진행 상황:

  docker compose logs -f vca-ai

전부 뜬 다음 헬스체크:

  curl -H "X-VCA-Access-Token: $VCA_ACCESS_TOKEN" http://localhost:8080/api/vca

Spring 기동 로그에 "Started ConservationBackendApplication"이 보이면 준비된
것이다. 그다음 scripts/smoke-vca-compose.sh로 업로드 -> run 생성까지
엔드투엔드 스모크 테스트를 돌려볼 수 있다:

  VCA_ACCESS_TOKEN="$VCA_ACCESS_TOKEN" bash scripts/smoke-vca-compose.sh

FE는 이 스크립트가 안 띄운다 - scripts/runpod-start-fe.sh를 따로 실행할 것.
EOF
