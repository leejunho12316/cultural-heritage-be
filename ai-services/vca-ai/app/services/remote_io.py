"""Presigned URL 기반 원격 파일 다운로드. ai-services/xray-ai/app/services/remote_io.py의
download()를 그대로 이식했다 - vca-ai도 AWS SDK 없이 순수 stdlib로 S3 presigned GET을 스트리밍
다운로드해서, EFS 같은 공유 볼륨 없이 Spring이 넘긴 이미지를 받을 수 있게 한다."""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


class RemoteIoError(RuntimeError):
    pass


def _validated_url(value: str) -> str:
    parsed = urlparse(str(value))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RemoteIoError(f"Only http/https URLs are allowed: {value!r}")
    return str(value)


def download(url: str, target: Path, max_bytes: int | None = None) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(_validated_url(url), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=300) as response, target.open("wb") as out:
            content_length = response.headers.get("Content-Length")
            if (
                max_bytes is not None
                and content_length is not None
                and int(content_length) > max_bytes
            ):
                raise RemoteIoError(
                    f"Download exceeded maximum size ({max_bytes} bytes): {target.name}"
                )

            written = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if max_bytes is not None and written > max_bytes:
                    raise RemoteIoError(
                        f"Download exceeded maximum size ({max_bytes} bytes): {target.name}"
                    )
                out.write(chunk)
    except RemoteIoError:
        target.unlink(missing_ok=True)
        raise
    except (OSError, ValueError, urllib.error.URLError) as exc:
        target.unlink(missing_ok=True)
        raise RemoteIoError(f"Failed to download {target.name}: {exc}") from exc
    return target
