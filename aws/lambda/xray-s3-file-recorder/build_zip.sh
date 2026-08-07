#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$ROOT/build"
OUT_DIR="$ROOT/dist"
OUT_ZIP="$OUT_DIR/xray-s3-file-recorder.zip"

rm -rf "$BUILD_DIR" "$OUT_DIR"
mkdir -p "$BUILD_DIR" "$OUT_DIR"

python -m pip install \
  --requirement "$ROOT/requirements.txt" \
  --target "$BUILD_DIR" \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.11 \
  --only-binary=:all:

cp "$ROOT/lambda_function.py" "$ROOT/repository.py" "$ROOT/s3_key.py" "$BUILD_DIR/"
(
  cd "$BUILD_DIR"
  python -m zipfile -c "$OUT_ZIP" .
)

echo "Created: $OUT_ZIP"
