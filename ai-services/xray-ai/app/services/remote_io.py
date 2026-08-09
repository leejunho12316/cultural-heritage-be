from __future__ import annotations

import http.client
import json
import shutil
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any
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


def upload(url: str, source: Path, content_type: str) -> None:
    """Stream a presigned PUT without loading large bundles into memory."""

    if not source.is_file():
        raise RemoteIoError(f"Upload source was not found: {source}")

    validated = _validated_url(url)
    parsed = urlparse(validated)
    connection_type = (
        http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    )
    connection = connection_type(parsed.hostname, parsed.port, timeout=300)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    try:
        size = source.stat().st_size
        connection.putrequest("PUT", path, skip_host=True, skip_accept_encoding=True)
        connection.putheader("Host", parsed.netloc)
        connection.putheader("Content-Type", content_type)
        connection.putheader("Content-Length", str(size))
        connection.endheaders()
        with source.open("rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                connection.send(chunk)
        response = connection.getresponse()
        response_body = response.read().decode("utf-8", errors="replace")
        if response.status < 200 or response.status >= 300:
            raise RemoteIoError(
                "Upload failed: "
                f"status={response.status}, file={source.name}, "
                f"body={response_body[:1000]}"
            )
    except RemoteIoError:
        raise
    except (OSError, http.client.HTTPException) as exc:
        raise RemoteIoError(f"Failed to upload {source.name}: {exc}") from exc
    finally:
        connection.close()


def callback(
    url: str,
    payload: dict[str, Any],
    token: str | None = None,
) -> None:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Xray-Callback-Token"] = token
    request = urllib.request.Request(
        _validated_url(url),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status < 200 or response.status >= 300:
                raise RemoteIoError(f"Callback failed: status={response.status}")
    except RemoteIoError:
        raise
    except (OSError, urllib.error.URLError) as exc:
        raise RemoteIoError(f"Callback failed: {exc}") from exc


def safe_extract_zip(
    zip_path: Path,
    destination: Path,
    max_uncompressed_bytes: int | None = None,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    try:
        with zipfile.ZipFile(zip_path) as archive:
            if max_uncompressed_bytes is not None:
                total_size = sum(member.file_size for member in archive.infolist())
                if total_size > max_uncompressed_bytes:
                    raise RemoteIoError(
                        "Finalization bundle exceeds the uncompressed size limit: "
                        f"{total_size} > {max_uncompressed_bytes}"
                    )

            for member in archive.infolist():
                # ZIP entries can encode Unix symlinks in external_attr. Never
                # materialize them into the process workspace.
                unix_mode = (member.external_attr >> 16) & 0o170000
                if unix_mode == 0o120000:
                    raise RemoteIoError(
                        f"Symlink is not allowed in finalization bundle: {member.filename}"
                    )
                member_path = (root / member.filename).resolve()
                try:
                    member_path.relative_to(root)
                except ValueError as exc:
                    raise RemoteIoError(
                        f"Unsafe path in finalization bundle: {member.filename}"
                    ) from exc
                if member.is_dir():
                    member_path.mkdir(parents=True, exist_ok=True)
                    continue
                member_path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as src, member_path.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    except RemoteIoError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise RemoteIoError(f"Invalid finalization bundle: {exc}") from exc
