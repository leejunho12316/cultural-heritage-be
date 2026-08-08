from __future__ import annotations

import http.server
import stat
import tempfile
import threading
import unittest
import uuid
import zipfile
from pathlib import Path

from app.schemas.stitch_request import FinalizationJobRequest, StitchJobRequest
from app.services.remote_io import RemoteIoError, download, safe_extract_zip, upload


class S3ContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.job_id = str(uuid.uuid4())
        self.artifact_id = str(uuid.uuid4())
        self.base = "https://example-bucket.s3.ap-northeast-2.amazonaws.com/object"

    def test_stitch_request_accepts_internal_callback_host(self) -> None:
        request = StitchJobRequest.model_validate(
            {
                "jobId": self.job_id,
                "artifactId": self.artifact_id,
                "configName": "config.json",
                "colorInput": {"fileName": "color.png", "downloadUrl": self.base},
                "xrayInputs": [
                    {"fileName": "piece-1.png", "downloadUrl": self.base + "1"},
                    {"fileName": "piece-2.png", "downloadUrl": self.base + "2"},
                ],
                "outputPutUrls": {
                    "assembled": self.base + "a",
                    "layout": self.base + "l",
                    "report": self.base + "r",
                    "layoutFragmentMasks": self.base + "b",
                },
                "callbackUrl": "http://conservation-backend:8080/api/xray/stitch/callback",
                "callbackToken": "token",
            }
        )
        self.assertEqual(self.job_id, request.jobId)
        self.assertEqual(2, len(request.xrayInputs))

    def test_stitch_request_allows_optional_report_output(self) -> None:
        request = StitchJobRequest.model_validate(
            {
                "jobId": self.job_id,
                "artifactId": self.artifact_id,
                "configName": "config.json",
                "colorInput": {"fileName": "color.png", "downloadUrl": self.base},
                "xrayInputs": [
                    {"fileName": "piece-1.png", "downloadUrl": self.base + "1"},
                    {"fileName": "piece-2.png", "downloadUrl": self.base + "2"},
                ],
                "outputPutUrls": {
                    "assembled": self.base + "a",
                    "layout": self.base + "l",
                    "layoutFragmentMasks": self.base + "b",
                },
                "callbackUrl": "http://conservation-backend:8080/api/xray/stitch/callback",
            }
        )
        self.assertIsNone(request.outputPutUrls.report)

    def test_finalization_request_contract(self) -> None:
        request = FinalizationJobRequest.model_validate(
            {
                "jobId": self.job_id,
                "artifactId": self.artifact_id,
                "xrayInputs": [
                    {"fileName": "piece-1.png", "downloadUrl": self.base + "1"},
                    {"fileName": "piece-2.png", "downloadUrl": self.base + "2"},
                ],
                "layoutFragmentMasksDownloadUrl": self.base + "masks",
                "finalLayoutDownloadUrl": self.base + "layout",
                "outputPutUrls": {
                    "assembledFinal": self.base + "final",
                    "sourceOwner": self.base + "source",
                    "fragmentOwner": self.base + "fragment",
                    "seamZone": self.base + "seam",
                    "overlapMask": self.base + "overlap",
                    "provenance": self.base + "provenance",
                },
                "callbackUrl": "http://conservation-backend:8080/api/xray/stitch/callback",
            }
        )
        self.assertEqual(self.artifact_id, request.artifactId)

    def test_streaming_upload_and_download(self) -> None:
        payload = b"x" * (2 * 1024 * 1024 + 17)
        received: dict[str, bytes | str] = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_PUT(self):
                length = int(self.headers["Content-Length"])
                received["content_type"] = self.headers["Content-Type"]
                received["body"] = self.rfile.read(length)
                self.send_response(200)
                self.end_headers()

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                return

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "source.bin"
                target = root / "target.bin"
                source.write_bytes(payload)
                url = f"http://127.0.0.1:{server.server_port}/object?signature=test"
                upload(url, source, "application/octet-stream")
                download(url, target, len(payload))
                self.assertEqual(payload, received["body"])
                self.assertEqual("application/octet-stream", received["content_type"])
                self.assertEqual(payload, target.read_bytes())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_download_limit_removes_partial_file(self) -> None:
        payload = b"too-large"

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                return

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                target = Path(temp) / "too-large.bin"
                url = f"http://127.0.0.1:{server.server_port}/object"
                with self.assertRaises(RemoteIoError):
                    download(url, target, max_bytes=1)
                self.assertFalse(target.exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_safe_extract_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../escape.txt", "no")
            with self.assertRaises(RemoteIoError):
                safe_extract_zip(archive, root / "out")

    def test_safe_extract_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "symlink.zip"
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr(info, "target")
            with self.assertRaises(RemoteIoError):
                safe_extract_zip(archive, root / "out")


if __name__ == "__main__":
    unittest.main()
