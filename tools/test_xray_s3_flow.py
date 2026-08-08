#!/usr/bin/env python3
"""Smoke-test Spring -> S3 -> FastAPI -> callback for one X-ray job.

This script tests the deployed S3-native base stitching flow. It does not
create a Konva final layout; finalization and defect mapping remain FE-driven.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def request_json(method: str, url: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} -> HTTP {exc.code}: {detail}") from exc
    return json.loads(data) if data else {}


def put_file(url: str, path: Path) -> None:
    # Input presigned URLs intentionally do not sign Content-Type.
    request = urllib.request.Request(url, data=path.read_bytes(), method="PUT")
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"S3 PUT failed: {path.name}, status={response.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"S3 PUT failed: {path.name}, HTTP {exc.code}: {detail}") from exc


def download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=300) as response, target.open("wb") as out:
        while chunk := response.read(1024 * 1024):
            out.write(chunk)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spring", default="http://localhost:8080")
    parser.add_argument("--artifact-id", required=True)
    parser.add_argument("--color", required=True, type=Path)
    parser.add_argument("--xray", required=True, nargs="+", type=Path)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--output", type=Path, default=Path("outputs/assembled_xray.png"))
    args = parser.parse_args()

    if not args.color.is_file():
        parser.error(f"Color file not found: {args.color}")
    if len(args.xray) < 2:
        parser.error("At least two --xray files are required")
    for path in args.xray:
        if not path.is_file():
            parser.error(f"X-ray file not found: {path}")

    base = args.spring.rstrip("/") + "/api/xray/stitch"
    prepared = request_json("POST", base + "/jobs/prepare", {
        "artifactId": args.artifact_id,
        "colorFileName": args.color.name,
        "xrayFileNames": [path.name for path in args.xray],
    })
    job_id = prepared["jobId"]
    print("prepared:", job_id)

    put_file(prepared["color"]["uploadUrl"], args.color)
    for target, source in zip(prepared["xrays"], args.xray):
        put_file(target["uploadUrl"], source)
    print("S3 inputs uploaded")

    request_json("POST", f"{base}/jobs/{job_id}/start", {
        "colorFileName": args.color.name,
        "xrayFileNames": [path.name for path in args.xray],
    })

    deadline = time.monotonic() + args.timeout
    last = None
    while time.monotonic() < deadline:
        status = request_json("GET", f"{base}/jobs/{job_id}")
        current = status.get("status")
        if current != last:
            print("status:", current, status.get("message", ""))
            last = current
        if current == "FAILED":
            print(status.get("errorMessage", "Unknown failure"), file=sys.stderr)
            return 1
        if current in {"COMPLETED", "FINALIZING", "FINALIZED"}:
            result = request_json("GET", f"{base}/jobs/{job_id}/result-url")
            download(result["url"], args.output)
            print("assembled result:", args.output.resolve())
            print("job:", json.dumps(status, ensure_ascii=False, indent=2))
            return 0
        time.sleep(2)

    print(f"Timed out after {args.timeout}s. Try POST {base}/jobs/{job_id}/reconcile", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
