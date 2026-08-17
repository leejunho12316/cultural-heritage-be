#!/usr/bin/env bash
# RunPod 팟 위에서 cultural-heritage-fe(Vite/React)를 개발 서버로 띄운다.
# scripts/runpod-bootstrap.sh로 backend 스택을 먼저 올린 뒤에 실행할 것.
#
# 사용법:
#   BACKEND_PUBLIC_URL=https://<pod-id>-8080.proxy.runpod.net \
#   FE_DIR=../cultural-heritage-fe \
#     bash scripts/runpod-start-fe.sh
#
# BACKEND_PUBLIC_URL은 RunPod 콘솔 Connect 탭에서 8080번 포트에 매핑된
# 프록시 URL이다(팟을 만들 때 8080/tcp를 HTTP 포트로 노출해 둬야 나온다).
# 이 값은 .env.runpod.example의 APP_CORS_ALLOWED_ORIGINS를 채울 때 쓴
# 5173(FE) 프록시 URL과는 다른 값이니 헷갈리지 말 것.
set -euo pipefail

FE_DIR="${FE_DIR:-../cultural-heritage-fe}"
BACKEND_PUBLIC_URL="${BACKEND_PUBLIC_URL:?BACKEND_PUBLIC_URL is required, e.g. https://<pod-id>-8080.proxy.runpod.net}"

if [ ! -d "$FE_DIR" ]; then
  echo "FE_DIR=$FE_DIR 를 못 찾았다. git clone으로 먼저 받아둘 것." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$REPO_ROOT/.env" ]; then
  # shellcheck disable=SC1090
  set -a; source "$REPO_ROOT/.env"; set +a
fi
VCA_ACCESS_TOKEN="${VCA_ACCESS_TOKEN:?backend .env에 VCA_ACCESS_TOKEN이 있어야 한다 - 먼저 runpod-bootstrap.sh를 실행할 것}"

cat > "$FE_DIR/.env" <<EOF
VITE_ARTIFACT_STORAGE_MODE=local
VITE_API_BASE_URL=${BACKEND_PUBLIC_URL}
VITE_ARTIFACTS_API_PATH=/api/artifacts

VITE_USE_XRAY_MOCK=false
VITE_USE_GUIDE_MOCK=false
VITE_USE_VCA_MOCK=false
VITE_VCA_ACCESS_TOKEN=${VCA_ACCESS_TOKEN}

VITE_XRAY_STITCH_API_BASE=${BACKEND_PUBLIC_URL}/api/xray/stitch
VITE_VIA_SPRING=true
VITE_XRAY_SPRING_INSPECTION_API_BASE=${BACKEND_PUBLIC_URL}/api/xray
EOF
echo "FE .env 작성 완료 ($FE_DIR/.env), VITE_API_BASE_URL=$BACKEND_PUBLIC_URL"

cd "$FE_DIR"
if [ ! -d node_modules ]; then
  npm install
fi

echo "vite dev server를 0.0.0.0:5173에 바인딩해서 띄운다 (RunPod 프록시가 여기로 붙는다)."
npm run dev -- --host 0.0.0.0 --port 5173
