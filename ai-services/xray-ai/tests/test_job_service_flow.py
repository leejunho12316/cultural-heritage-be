from __future__ import annotations

import http.server
import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

from app.schemas.stitch_request import FinalizationJobRequest, StitchJobRequest
from app.services.job_service import JobService
from app.services.stitcher import StitchExecutionResult


class _State:
    get_payloads: dict[str, bytes]
    puts: dict[str, tuple[str, bytes]]
    callbacks: list[dict]

    def __init__(self) -> None:
        self.get_payloads = {}
        self.puts = {}
        self.callbacks = []


class JobServiceFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = _State()
        state = self.state

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                payload = state.get_payloads.get(self.path)
                if payload is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_PUT(self):
                length = int(self.headers["Content-Length"])
                body = self.rfile.read(length)
                state.puts[self.path] = (self.headers.get("Content-Type", ""), body)
                self.send_response(200)
                self.end_headers()

            def do_POST(self):
                length = int(self.headers["Content-Length"])
                state.callbacks.append(json.loads(self.rfile.read(length)))
                self.send_response(200)
                self.end_headers()

            def log_message(self, format, *args):
                return

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_stitch_and_finalization_round_trip(self) -> None:
        job_id = str(uuid.uuid4())
        artifact_id = str(uuid.uuid4())
        self.state.get_payloads["/color"] = b"color"
        self.state.get_payloads["/xray/1"] = b"fragment-1"
        self.state.get_payloads["/xray/2"] = b"fragment-2"

        class FakeStitcher:
            def run(self, request):
                artifact_dir = (
                    Path(request.outputDirectory) / "assembly" / "artifacts" / request.artifactId
                )
                artifact_dir.mkdir(parents=True, exist_ok=True)
                assembled = artifact_dir / "assembled_xray.png"
                layout = artifact_dir / "layout.json"
                report = artifact_dir / "report.json"
                assembled.write_bytes(b"assembled")
                layout.write_text(
                    json.dumps({"canvas": {"width": 10, "height": 10}, "fragments": []})
                )
                report.write_text(json.dumps({"status": "completed"}))
                masks = artifact_dir / "debug" / "layout_fragment_masks"
                masks.mkdir(parents=True, exist_ok=True)
                (masks / "fragment_0000.png").write_bytes(b"mask")
                return StitchExecutionResult(
                    return_code=0,
                    output_dir=str(artifact_dir.parents[2]),
                    report_path=str(report),
                    layout_path=str(layout),
                    assembled_image_path=str(assembled),
                    stdout_log="",
                    stderr_log="",
                )

        class FakeFinalizer:
            def __init__(self, root: Path) -> None:
                self.root = root

            def finalize(self, current_job_id: str):
                artifact_dir = (
                    self.root / current_job_id / "outputs" / "assembly" / "artifacts" / artifact_id
                )
                outputs = {
                    "assembled_xray.final.png": b"final",
                    "source_owner.final.png": b"source",
                    "fragment_owner.final.png": b"fragment",
                    "seam_zone.final.png": b"seam",
                    "overlap_mask.final.png": b"overlap",
                    "provenance.final.json": b"{}",
                }
                for name, payload in outputs.items():
                    (artifact_dir / name).write_bytes(payload)
                return {}

        with tempfile.TemporaryDirectory() as temp:
            jobs_root = Path(temp) / "jobs"
            service = JobService(
                stitcher=FakeStitcher(),
                finalizer=FakeFinalizer(jobs_root),
                jobs_root=jobs_root,
            )
            stitch_request = StitchJobRequest.model_validate(
                {
                    "jobId": job_id,
                    "artifactId": artifact_id,
                    "configName": "config.json",
                    "colorInput": {"fileName": "color.png", "downloadUrl": self.base + "/color"},
                    "xrayInputs": [
                        {"fileName": "piece-1.png", "downloadUrl": self.base + "/xray/1"},
                        {"fileName": "piece-2.png", "downloadUrl": self.base + "/xray/2"},
                    ],
                    "outputPutUrls": {
                        "assembled": self.base + "/put/assembled",
                        "layout": self.base + "/put/layout",
                        "report": self.base + "/put/report",
                        "layoutFragmentMasks": self.base + "/put/masks",
                    },
                    "callbackUrl": self.base + "/callback",
                }
            )
            _, should_run = service.accept_stitch_job(stitch_request)
            self.assertTrue(should_run)
            service.run_stitch_job(stitch_request)
            self.assertEqual("COMPLETED", service.get_status(job_id).status)
            self.assertEqual(b"assembled", self.state.puts["/put/assembled"][1])
            self.assertIn("/put/masks", self.state.puts)
            self.assertEqual("COMPLETED", self.state.callbacks[-1]["status"])

            self.state.get_payloads["/masks"] = self.state.puts["/put/masks"][1]
            self.state.get_payloads["/layout-final"] = json.dumps(
                {
                    "canvas": {"width": 10, "height": 10},
                    "fragments": [],
                    "layoutStage": "FINAL",
                }
            ).encode()
            final_request = FinalizationJobRequest.model_validate(
                {
                    "jobId": job_id,
                    "artifactId": artifact_id,
                    "xrayInputs": [
                        {"fileName": "piece-1.png", "downloadUrl": self.base + "/xray/1"},
                        {"fileName": "piece-2.png", "downloadUrl": self.base + "/xray/2"},
                    ],
                    "layoutFragmentMasksDownloadUrl": self.base + "/masks",
                    "finalLayoutDownloadUrl": self.base + "/layout-final",
                    "outputPutUrls": {
                        "assembledFinal": self.base + "/put/final",
                        "sourceOwner": self.base + "/put/source",
                        "fragmentOwner": self.base + "/put/fragment",
                        "seamZone": self.base + "/put/seam",
                        "overlapMask": self.base + "/put/overlap",
                        "provenance": self.base + "/put/provenance",
                    },
                    "callbackUrl": self.base + "/callback",
                }
            )
            service.accept_finalization_job(final_request)
            service.run_finalization_job(final_request)
            self.assertEqual("FINALIZED", service.get_status(job_id).status)
            self.assertEqual(b"final", self.state.puts["/put/final"][1])
            self.assertEqual("FINALIZED", self.state.callbacks[-1]["status"])


if __name__ == "__main__":
    unittest.main()
