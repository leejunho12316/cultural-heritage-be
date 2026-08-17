#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${VCA_SMOKE_BASE_URL:-http://localhost:8080}"
TOKEN="${VCA_ACCESS_TOKEN:?VCA_ACCESS_TOKEN is required for VCA smoke test}"
ARTIFACT_ID="vca-smoke-$(date +%s)"
IMAGE_FILE="$(mktemp -t vca-smoke-image.XXXXXX.png)"

cleanup() {
  rm -f "$IMAGE_FILE"
}
trap cleanup EXIT

python3 - "$IMAGE_FILE" <<'PY'
import struct
import sys
import zlib
from pathlib import Path


def chunk(name: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(name + payload) & 0xFFFFFFFF
    return struct.pack("!I", len(payload)) + name + payload + struct.pack("!I", checksum)


png = b"\x89PNG\r\n\x1a\n"
png += chunk(b"IHDR", struct.pack("!IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
png += chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff\xff"))
png += chunk(b"IEND", b"")
Path(sys.argv[1]).write_bytes(png)
PY

unauthorized_status="$(curl -sS -o /dev/null -w '%{http_code}' "$BASE_URL/api/vca/$ARTIFACT_ID")"
if [[ "$unauthorized_status" != "401" ]]; then
  printf 'Expected unauthenticated VCA request to return 401, got %s\n' "$unauthorized_status" >&2
  exit 1
fi

upload_response="$(curl -sS -X POST \
  -H "X-VCA-Access-Token: $TOKEN" \
  -F "file=@$IMAGE_FILE;type=image/png;filename=front.png" \
  "$BASE_URL/api/vca/$ARTIFACT_ID/images")"
image_id="$(printf '%s' "$upload_response" | python3 -c 'import json,sys; print(json.load(sys.stdin)["imageId"])')"
if [[ -z "$image_id" ]]; then
  printf 'Upload did not return imageId: %s\n' "$upload_response" >&2
  exit 1
fi

run_response="$(curl -sS -X POST \
  -H "X-VCA-Access-Token: $TOKEN" \
  "$BASE_URL/api/vca/$ARTIFACT_ID/runs")"
run_id="$(printf '%s' "$run_response" | python3 -c 'import json,sys; print(json.load(sys.stdin)["assessmentRunId"])')"
if [[ -z "$run_id" ]]; then
  printf 'Run creation did not return assessmentRunId: %s\n' "$run_response" >&2
  exit 1
fi

curl -sS -f \
  -H "X-VCA-Access-Token: $TOKEN" \
  "$BASE_URL/api/vca/$ARTIFACT_ID/runs/$run_id/intermediate-results" >/dev/null

printf 'VCA smoke passed for artifactId=%s assessmentRunId=%s imageId=%s\n' \
  "$ARTIFACT_ID" "$run_id" "$image_id"
