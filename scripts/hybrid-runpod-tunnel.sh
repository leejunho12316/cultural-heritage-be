#!/usr/bin/env bash
# 하이브리드 구성(vca-ai만 RunPod, 나머지는 로컬/AWS)에 필요한 양방향 SSH
# 터널을 연다. docker-compose.hybrid-runpod.yml과 짝을 이룬다.
#
#   -L (local forward): 맥북의 localhost:$VCA_AI_TUNNEL_LOCAL_PORT ->
#     팟의 localhost:$VCA_AI_REMOTE_PORT(vca-ai FastAPI). 백엔드 컨테이너가
#     host.docker.internal:$VCA_AI_TUNNEL_LOCAL_PORT로 vca-ai를 호출하게 한다.
#   -R (reverse forward): 팟의 localhost:$MINIO_REMOTE_PORT ->
#     맥북의 localhost:$MINIO_LOCAL_PORT(실제 MinIO). vca-ai가 백엔드로부터
#     받은 presigned URL(http://localhost:9000/...)을 그대로 열면 이 터널을
#     통해 맥북의 MinIO에 닿는다.
#
# 사용법:
#   POD_HOST=213.173.102.238 POD_PORT=26997 POD_SSH_KEY=~/.ssh/id_ed25519 \
#     bash scripts/hybrid-runpod-tunnel.sh
#
# 포그라운드로 붙어있는 동안만 터널이 산다 - 세션 내내 유지하려면 별도
# 터미널/백그라운드 job으로 띄워둘 것.
set -euo pipefail

POD_HOST="${POD_HOST:?POD_HOST is required (RunPod pod public IP)}"
POD_PORT="${POD_PORT:?POD_PORT is required (RunPod pod SSH port)}"
POD_SSH_KEY="${POD_SSH_KEY:-$HOME/.ssh/id_ed25519}"

VCA_AI_TUNNEL_LOCAL_PORT="${VCA_AI_TUNNEL_LOCAL_PORT:-18000}"
VCA_AI_REMOTE_PORT="${VCA_AI_REMOTE_PORT:-8000}"
MINIO_LOCAL_PORT="${MINIO_LOCAL_PORT:-9000}"
MINIO_REMOTE_PORT="${MINIO_REMOTE_PORT:-9000}"

echo "tunnel: local  localhost:${VCA_AI_TUNNEL_LOCAL_PORT} -> pod localhost:${VCA_AI_REMOTE_PORT} (vca-ai)"
echo "tunnel: reverse pod localhost:${MINIO_REMOTE_PORT} -> local localhost:${MINIO_LOCAL_PORT} (minio)"

exec ssh \
  -p "$POD_PORT" \
  -i "$POD_SSH_KEY" \
  -o StrictHostKeyChecking=no \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -N \
  -L "${VCA_AI_TUNNEL_LOCAL_PORT}:localhost:${VCA_AI_REMOTE_PORT}" \
  -R "${MINIO_REMOTE_PORT}:localhost:${MINIO_LOCAL_PORT}" \
  "root@${POD_HOST}"
