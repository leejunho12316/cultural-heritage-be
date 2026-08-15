import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.services import assessment_runs, vca_process


client = TestClient(app)


@pytest.fixture(autouse=True)
def _run_background_launch_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the pipeline launch synchronously so assertions are deterministic.

    Production code backgrounds the run via a real thread (see
    assessment_runs._launch_background); tests that want to assert on
    side effects right after the HTTP call opt into that here instead of
    racing a real thread.
    """

    def launch_inline(
        run: object,
        input_directory: object,
        settings: object,
        resume_from_stage: object,
    ) -> None:
        assessment_runs._run_vca_and_record_failure(
            run, input_directory, settings, resume_from_stage
        )

    monkeypatch.setattr(assessment_runs, "_launch_background", launch_inline)


def create_input_folder(shared_root: Path) -> Path:
    input_folder = shared_root / "run-123" / "input"
    input_folder.mkdir(parents=True)
    (input_folder / "front.jpg").write_bytes(b"vca image bytes")
    return input_folder


def test_health_when_service_is_requested() -> None:
    # Given: the VCA adapter is running
    # When: its health endpoint is requested
    response = client.get("/health")

    # Then: its existing health contract remains available
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "vca-ai", "mode": "deterministic"}


def test_assessment_run_when_created_in_default_full_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an image folder and neutral full-run timeout configuration
    shared_root = tmp_path / "shared" / "vca"
    input_folder = create_input_folder(shared_root)
    engine_root = tmp_path / "vca_v2"
    project_name = "artifact-123-run-123"
    stale_output = engine_root / "output" / "result" / project_name
    stale_output.mkdir(parents=True)
    (stale_output / "startup.json").write_text("stale", encoding="utf-8")
    captured_command: list[str] = []

    def fake_run(
        command: list[str], *, cwd: Path, timeout: int, run_id: str
    ) -> subprocess.CompletedProcess[str]:
        captured_command.extend(command)
        assert cwd == engine_root
        assert not stale_output.exists()
        assert timeout == 600
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setenv("VCA_RUN_TIMEOUT_SECONDS", "600")
    monkeypatch.setenv("VCA_DRY_RUN_TIMEOUT_SECONDS", "120")
    monkeypatch.setattr(vca_process, "_run_command", fake_run)

    # When: Spring creates an assessment run
    response = client.post(
        "/internal/vca/assessment-runs",
        json={"assessmentId": "artifact-123", "projectName": project_name, "inputImageFolder": str(input_folder)},
    )

    # Then: the real full-run entrypoint is invoked without dry-run options
    assert response.status_code == 202
    assert response.json()["assessmentId"] == "artifact-123"
    assert response.json()["runId"] == "vca-artifact-123~artifact-123-run-123"
    assert captured_command == [
        "uv", "run", "python", "-m", "modules.orchestration.startup", project_name, str(input_folder.resolve())
    ]


def test_assessment_run_when_optional_engine_settings_are_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: device, image-limit, and model-cache environment settings
    shared_root = tmp_path / "shared" / "vca"
    input_folder = create_input_folder(shared_root)
    engine_root = tmp_path / "vca_v2"
    captured_command: list[str] = []

    def fake_run(
        command: list[str], *, cwd: Path, timeout: int, run_id: str
    ) -> subprocess.CompletedProcess[str]:
        _ = cwd, timeout
        captured_command.extend(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setenv("VCA_DEVICE", "cpu")
    monkeypatch.setenv("VCA_MAX_IMAGES", "2")
    monkeypatch.setenv("VCA_MODEL_CACHE_ROOT", "/opt/vca-models/models")
    monkeypatch.setattr(vca_process, "_run_command", fake_run)

    # When: Spring creates a run
    response = client.post(
        "/internal/vca/assessment-runs",
        json={"assessmentId": "artifact-123", "projectName": "artifact-123-run-123", "inputImageFolder": str(input_folder)},
    )

    # Then: only explicitly configured startup options are forwarded
    assert response.status_code == 202
    assert "--device" in captured_command
    assert captured_command[captured_command.index("--device") + 1] == "cpu"
    assert "--max-images" in captured_command
    assert captured_command[captured_command.index("--max-images") + 1] == "2"
    assert "--model-cache-root" in captured_command
    assert captured_command[captured_command.index("--model-cache-root") + 1] == "/opt/vca-models/models"
    assert "--dry-run" not in captured_command


def _write_prior_startup_receipt(
    engine_root: Path, prior_project_name: str, stage_statuses: dict[str, str]
) -> None:
    receipt_dir = engine_root / "output" / "result" / prior_project_name / "receipts"
    receipt_dir.mkdir(parents=True)
    stages = [
        {"name": name, "status": status} for name, status in stage_statuses.items()
    ]
    (receipt_dir / "startup.json").write_text(
        json.dumps({"schema": "vca-startup-receipt-v1", "stages": stages}),
        encoding="utf-8",
    )


def _write_prior_stage_output(
    engine_root: Path, prior_project_name: str, stage_name: str
) -> None:
    stage_dir = engine_root / "output" / stage_name / prior_project_name
    stage_dir.mkdir(parents=True)
    (stage_dir / "marker.txt").write_text(stage_name, encoding="utf-8")


def test_assessment_run_resumes_from_prior_failed_run_when_stages_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a prior run that completed preprocessing and rough_masking, then
    # failed at visual_cue_generation.
    shared_root = tmp_path / "shared" / "vca"
    input_folder = create_input_folder(shared_root)
    engine_root = tmp_path / "vca_v2"
    prior_project_name = "artifact-123-run-122"
    _write_prior_startup_receipt(
        engine_root,
        prior_project_name,
        {
            "preprocessing": "completed",
            "rough_masking": "completed",
            "visual_cue_generation": "failed",
        },
    )
    _write_prior_stage_output(engine_root, prior_project_name, "preprocessing")
    _write_prior_stage_output(engine_root, prior_project_name, "rough_masking")
    captured_command: list[str] = []

    def fake_run(
        command: list[str], *, cwd: Path, timeout: int, run_id: str
    ) -> subprocess.CompletedProcess[str]:
        _ = cwd, timeout, run_id
        captured_command.extend(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setattr(vca_process, "_run_command", fake_run)

    # When: Spring creates the next run for the same artifact, pointing back
    # at the prior failed run's project name.
    new_project_name = "artifact-123-run-123"
    response = client.post(
        "/internal/vca/assessment-runs",
        json={
            "assessmentId": "artifact-123",
            "projectName": new_project_name,
            "inputImageFolder": str(input_folder),
            "resumeFromProjectName": prior_project_name,
        },
    )

    # Then: the completed stages are copied into the new project and vca_v2
    # is told to resume from the stage that actually failed.
    assert response.status_code == 202
    assert "--resume-from-stage" in captured_command
    assert (
        captured_command[captured_command.index("--resume-from-stage") + 1]
        == "visual_cue_generation"
    )
    new_output_root = engine_root / "output"
    assert (
        new_output_root / "preprocessing" / new_project_name / "marker.txt"
    ).read_text(encoding="utf-8") == "preprocessing"
    assert (
        new_output_root / "rough_masking" / new_project_name / "marker.txt"
    ).read_text(encoding="utf-8") == "rough_masking"
    assert (
        new_output_root
        / "result"
        / new_project_name
        / "receipts"
        / "startup.json"
    ).is_file()


def test_assessment_run_falls_back_to_full_run_when_resume_source_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Spring points at a resume source that has no startup.json at all
    # (eg. its output was cleaned up, or the id is stale).
    shared_root = tmp_path / "shared" / "vca"
    input_folder = create_input_folder(shared_root)
    engine_root = tmp_path / "vca_v2"
    project_name = "artifact-123-run-123"
    stale_output = engine_root / "output" / "result" / project_name
    stale_output.mkdir(parents=True)
    (stale_output / "startup.json").write_text("stale", encoding="utf-8")
    captured_command: list[str] = []

    def fake_run(
        command: list[str], *, cwd: Path, timeout: int, run_id: str
    ) -> subprocess.CompletedProcess[str]:
        _ = cwd, timeout, run_id
        captured_command.extend(command)
        assert not stale_output.exists()
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setattr(vca_process, "_run_command", fake_run)

    # When: Spring still asks to resume from a nonexistent prior project.
    response = client.post(
        "/internal/vca/assessment-runs",
        json={
            "assessmentId": "artifact-123",
            "projectName": project_name,
            "inputImageFolder": str(input_folder),
            "resumeFromProjectName": "artifact-123-run-does-not-exist",
        },
    )

    # Then: the run still starts, but as a normal full run.
    assert response.status_code == 202
    assert "--resume-from-stage" not in captured_command


def test_assessment_run_when_local_unverified_model_hashes_are_allowed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: local wiring verification explicitly allows unverified model cache hashes.
    shared_root = tmp_path / "shared" / "vca"
    input_folder = create_input_folder(shared_root)
    engine_root = tmp_path / "vca_v2"
    captured_command: list[str] = []

    def fake_run(
        command: list[str], *, cwd: Path, timeout: int, run_id: str
    ) -> subprocess.CompletedProcess[str]:
        _ = cwd, timeout
        captured_command.extend(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setenv("VCA_LOCAL_ALLOW_UNVERIFIED_MODEL_HASHES", "true")
    monkeypatch.setattr(vca_process, "_run_command", fake_run)

    # When: Spring creates a run through the local adapter.
    response = client.post(
        "/internal/vca/assessment-runs",
        json={"assessmentId": "artifact-123", "projectName": "artifact-123-run-123", "inputImageFolder": str(input_folder)},
    )

    # Then: the local-only startup bypass flag is forwarded.
    assert response.status_code == 202
    assert "--allow-unverified-model-hashes-local-only" in captured_command


def test_assessment_run_when_dry_run_mode_is_explicitly_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: dry-run mode with real-only device and shared image-limit settings
    shared_root = tmp_path / "shared" / "vca"
    input_folder = create_input_folder(shared_root)
    engine_root = tmp_path / "vca_v2"
    captured_command: list[str] = []

    def fake_run(
        command: list[str], *, cwd: Path, timeout: int, run_id: str
    ) -> subprocess.CompletedProcess[str]:
        _ = cwd, timeout
        captured_command.extend(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setenv("VCA_RUN_MODE", "dry-run")
    monkeypatch.setenv("VCA_DEVICE", "cuda")
    monkeypatch.setenv("VCA_MAX_IMAGES", "2")
    monkeypatch.setattr(vca_process, "_run_command", fake_run)

    # When: Spring creates a dry-run assessment
    response = client.post(
        "/internal/vca/assessment-runs",
        json={"assessmentId": "artifact-123", "projectName": "artifact-123-run-123", "inputImageFolder": str(input_folder)},
    )

    # Then: startup receives dry-run and image-limit flags without a device request
    assert response.status_code == 202
    assert captured_command[-3:] == ["--dry-run", "--max-images", "2"]
    assert "--device" not in captured_command


def test_assessment_run_when_real_startup_exits_non_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: default real mode and a startup process that cannot access a GPU
    shared_root = tmp_path / "shared" / "vca"
    input_folder = create_input_folder(shared_root)
    engine_root = tmp_path / "vca_v2"
    captured_command: list[str] = []

    def fake_run(
        command: list[str], *, cwd: Path, timeout: int, run_id: str
    ) -> subprocess.CompletedProcess[str]:
        _ = cwd, timeout
        captured_command.extend(command)
        raise subprocess.CalledProcessError(2, command, output="", stderr="GPU unavailable")

    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setattr(vca_process, "_run_command", fake_run)

    # When: Spring creates a real assessment run
    response = client.post(
        "/internal/vca/assessment-runs",
        json={"assessmentId": "artifact-123", "projectName": "artifact-123-run-123", "inputImageFolder": str(input_folder)},
    )

    # Then: the run is still accepted (it runs in the background); the
    # non-zero process exit surfaces as a polled FAILED status instead of a
    # synchronous gateway error.
    assert response.status_code == 202
    assert response.json()["status"] == "FAILED"
    assert "GPU unavailable" in response.json()["failureReason"]
    assert "--dry-run" not in captured_command


def test_assessment_run_when_cancelled_reports_cancelled_by_user_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a run cancel_run() already marked cancelled right before its
    # subprocess exits non-zero from the SIGTERM it sent.
    shared_root = tmp_path / "shared" / "vca"
    input_folder = create_input_folder(shared_root)
    engine_root = tmp_path / "vca_v2"
    run_id = "vca-artifact-123~artifact-123-run-123"
    vca_process._cancelled_run_ids.add(run_id)

    def fake_run(
        command: list[str], *, cwd: Path, timeout: int, run_id: str
    ) -> subprocess.CompletedProcess[str]:
        _ = cwd, timeout, run_id
        raise subprocess.CalledProcessError(-15, command, output="", stderr="Terminated")

    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setattr(vca_process, "_run_command", fake_run)

    try:
        # When: Spring creates the run that gets cancelled mid-flight.
        response = client.post(
            "/internal/vca/assessment-runs",
            json={"assessmentId": "artifact-123", "projectName": "artifact-123-run-123", "inputImageFolder": str(input_folder)},
        )

        # Then: the failure reason is the user-facing cancellation message,
        # not the raw subprocess stderr.
        assert response.status_code == 202
        assert response.json()["status"] == "FAILED"
        assert vca_process._CANCELLED_BY_USER_REASON in response.json()["failureReason"]
        assert "Terminated" not in response.json()["failureReason"]
    finally:
        vca_process._cancelled_run_ids.discard(run_id)


def test_cancel_run_signals_the_active_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a run with an actively tracked, still-alive subprocess.
    killed_groups: list[tuple[int, int]] = []

    class RunningProcess:
        pid = 24680

        def poll(self) -> int | None:
            return None

    monkeypatch.setattr(
        vca_process.os, "killpg", lambda pid, sig: killed_groups.append((pid, sig))
    )
    vca_process._mark_cancellable("run-cancel-1", RunningProcess())

    try:
        # When: the run is cancelled.
        cancelled = vca_process.cancel_run("run-cancel-1")

        # Then: SIGTERM reaches the process group and the run is remembered
        # as cancelled so run_vca() can report a clean reason.
        assert cancelled is True
        assert killed_groups == [(24680, vca_process.signal.SIGTERM)]
        assert vca_process._was_cancelled("run-cancel-1") is True
    finally:
        vca_process._forget_cancellable("run-cancel-1")


def test_cancel_run_is_a_no_op_when_no_process_is_tracked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: no subprocess registered for this run id (never launched, or
    # this adapter instance did not launch it).
    killed_groups: list[tuple[int, int]] = []
    monkeypatch.setattr(
        vca_process.os, "killpg", lambda pid, sig: killed_groups.append((pid, sig))
    )

    # When/Then: cancelling reports nothing to cancel and signals nobody.
    assert vca_process.cancel_run("run-never-started") is False
    assert killed_groups == []


def test_cancel_run_is_a_no_op_once_the_process_already_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a tracked process that already finished on its own.
    killed_groups: list[tuple[int, int]] = []

    class FinishedProcess:
        pid = 13579

        def poll(self) -> int | None:
            return 0

    monkeypatch.setattr(
        vca_process.os, "killpg", lambda pid, sig: killed_groups.append((pid, sig))
    )
    vca_process._mark_cancellable("run-cancel-2", FinishedProcess())

    try:
        # When/Then: cancelling an already-finished run signals nothing.
        assert vca_process.cancel_run("run-cancel-2") is False
        assert killed_groups == []
    finally:
        vca_process._forget_cancellable("run-cancel-2")


def test_cancel_run_endpoint_returns_current_run_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an engine root with no progress.json yet for this run id (no
    # subprocess is tracked for it in this adapter instance either).
    engine_root = tmp_path / "vca_v2"
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    run_id = "vca-artifact-cancel~artifact-cancel-run-1"

    # When: a cancel request arrives for it.
    response = client.post(f"/internal/vca/assessment-runs/{run_id}/cancel")

    # Then: it responds with the run's current status instead of erroring,
    # so a stale/duplicate stop click stays harmless.
    assert response.status_code == 200
    assert response.json()["runId"] == run_id
    assert response.json()["status"] == "RUNNING"


def test_cancel_run_endpoint_rejects_unknown_run_id_shape() -> None:
    response = client.post("/internal/vca/assessment-runs/not-a-real-run-id/cancel")

    assert response.status_code == 404


def test_run_command_when_timeout_kills_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a native startup process exceeds the configured timeout.
    killed_groups: list[tuple[int, int]] = []

    class TimeoutProcess:
        pid = 12345
        returncode = None
        communicate_calls = 0

        def communicate(self, *, timeout: int | None = None) -> tuple[str, str]:
            self.communicate_calls += 1
            if timeout == 1:
                raise subprocess.TimeoutExpired(["uv"], 1)
            if timeout == vca_process._PROCESS_TERMINATION_GRACE_SECONDS:
                raise subprocess.TimeoutExpired(["uv"], timeout)
            return "", ""

    def fake_popen(
        command: list[str],
        *,
        cwd: Path,
        stdout: int,
        stderr: int,
        text: bool,
        start_new_session: bool,
    ) -> TimeoutProcess:
        assert command == ["uv"]
        assert cwd == tmp_path
        assert (stdout, stderr, text, start_new_session) == (
            subprocess.PIPE,
            subprocess.PIPE,
            True,
            True,
        )
        return TimeoutProcess()

    def fake_killpg(pid: int, sig: int) -> None:
        killed_groups.append((pid, sig))

    monkeypatch.setattr(vca_process.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(vca_process.os, "killpg", fake_killpg)

    # When/Then: the timeout is re-raised after killing the whole process group.
    with pytest.raises(subprocess.TimeoutExpired):
        vca_process._run_command(
            ["uv"],
            cwd=tmp_path,
            timeout=1,
            run_id="test-run",
        )
    assert killed_groups == [
        (12345, vca_process.signal.SIGTERM),
        (12345, vca_process.signal.SIGKILL),
    ]


def test_assessment_run_when_run_mode_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an unsupported runtime mode
    shared_root = tmp_path / "shared" / "vca"
    input_folder = create_input_folder(shared_root)
    monkeypatch.setenv("VCA_RUN_MODE", "preview")

    # When: Spring creates an assessment run
    response = client.post(
        "/internal/vca/assessment-runs",
        json={"assessmentId": "artifact-123", "projectName": "artifact-123-run-123", "inputImageFolder": str(input_folder)},
    )

    # Then: configuration errors surface as a bad gateway response
    assert response.status_code == 502
    assert "VCA_RUN_MODE" in response.json()["detail"]


def test_assessment_run_when_stage_outputs_are_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: every known VCA stage has stale output for the current project
    shared_root = tmp_path / "shared" / "vca"
    input_folder = create_input_folder(shared_root)
    engine_root = tmp_path / "vca_v2"
    project_name = "artifact-123-run-123"
    sibling_name = "artifact-456-run-456"
    for stage in assessment_runs.ENGINE_OUTPUT_STAGES:
        for name, content in ((project_name, "stale"), (sibling_name, "keep")):
            output_directory = engine_root / "output" / stage / name
            output_directory.mkdir(parents=True)
            (output_directory / "output.txt").write_text(content, encoding="utf-8")

    def fake_run(
        command: list[str], *, cwd: Path, timeout: int, run_id: str
    ) -> subprocess.CompletedProcess[str]:
        _ = cwd, timeout
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setattr(vca_process, "_run_command", fake_run)

    # When: Spring creates an internal assessment run for that project
    response = client.post(
        "/internal/vca/assessment-runs",
        json={"assessmentId": "artifact-123", "projectName": project_name, "inputImageFolder": str(input_folder)},
    )

    # Then: only the current project's stale output directories are cleared
    assert response.status_code == 202
    for stage in assessment_runs.ENGINE_OUTPUT_STAGES:
        assert not (engine_root / "output" / stage / project_name).exists()
        assert (engine_root / "output" / stage / sibling_name).exists()


def test_assessment_run_when_input_folder_is_outside_shared_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Spring passes a path outside the configured shared storage root
    shared_root = tmp_path / "shared" / "vca"
    outside_folder = tmp_path / "outside" / "input"
    outside_folder.mkdir(parents=True)
    (outside_folder / "front.jpg").write_bytes(b"vca image bytes")
    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))

    # When: Spring creates an internal assessment run
    response = client.post(
        "/internal/vca/assessment-runs",
        json={"assessmentId": "artifact-123", "projectName": "artifact-123-run-123", "inputImageFolder": str(outside_folder)},
    )

    # Then: the adapter rejects the unsafe folder
    assert response.status_code == 400
    assert "VCA_SHARED_STORAGE_ROOT" in response.json()["detail"]


def test_assessment_run_when_input_folder_has_no_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Spring passes an empty shared input folder
    shared_root = tmp_path / "shared" / "vca"
    input_folder = shared_root / "run-123" / "input"
    input_folder.mkdir(parents=True)
    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))

    # When: Spring creates an internal assessment run
    response = client.post(
        "/internal/vca/assessment-runs",
        json={"assessmentId": "artifact-123", "projectName": "artifact-123-run-123", "inputImageFolder": str(input_folder)},
    )

    # Then: the adapter rejects the folder before invoking VCA
    assert response.status_code == 400
    assert "no supported images" in response.json()["detail"]


def test_assessment_status_when_run_identifier_is_unknown() -> None:
    # Given: a non-VCA run identifier
    # When: Spring polls the assessment run
    response = client.get("/internal/vca/assessment-runs/unknown")

    # Then: the adapter rejects it as an unknown run
    assert response.status_code == 404


def _write_progress(
    engine_root: Path,
    project_name: str,
    *,
    status: str,
    current_stage: str | None,
    stages: list[dict[str, object]],
) -> None:
    progress_path = (
        engine_root / "output" / "result" / project_name / "receipts" / "progress.json"
    )
    progress_path.parent.mkdir(parents=True)
    progress_path.write_text(
        json.dumps(
            {
                "schema": "vca-startup-progress-v1",
                "project_name": project_name,
                "status": status,
                "current_stage": current_stage,
                "stages": stages,
            }
        ),
        encoding="utf-8",
    )


def test_assessment_status_reflects_running_engine_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the engine has written an in-flight progress snapshot.
    engine_root = tmp_path / "vca_v2"
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    _write_progress(
        engine_root,
        "artifact-123-run-123",
        status="running",
        current_stage="rag",
        stages=[
            {"name": "preprocessing", "status": "completed", "exit_code": 0},
            {"name": "rough_masking", "status": "completed", "exit_code": 0},
        ],
    )

    # When: Spring polls the run status mid-flight.
    response = client.get(
        "/internal/vca/assessment-runs/vca-artifact-123~artifact-123-run-123"
    )

    # Then: the real in-progress stage detail is surfaced, not hardcoded COMPLETED.
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "RUNNING"
    assert body["currentStage"] == "rag"
    assert [stage["name"] for stage in body["stages"]] == [
        "preprocessing",
        "rough_masking",
    ]
    assert [stage["status"] for stage in body["stages"]] == [
        "completed",
        "completed",
    ]


def test_assessment_status_reflects_completed_engine_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the engine finished every stage and wrote a completed snapshot.
    engine_root = tmp_path / "vca_v2"
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    _write_progress(
        engine_root,
        "artifact-123-run-123",
        status="completed",
        current_stage=None,
        stages=[{"name": "report_generating", "status": "completed", "exit_code": 0}],
    )

    # When: Spring polls the run status after completion.
    response = client.get(
        "/internal/vca/assessment-runs/vca-artifact-123~artifact-123-run-123"
    )

    # Then: completion is reported from the real snapshot.
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    assert response.json()["currentStage"] is None


def test_assessment_status_defaults_to_running_before_any_progress_is_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a valid run identifier whose engine has not written progress yet.
    engine_root = tmp_path / "vca_v2"
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))

    # When: Spring polls immediately after the run was accepted.
    response = client.get(
        "/internal/vca/assessment-runs/vca-artifact-999~artifact-999-run-999"
    )

    # Then: the adapter reports RUNNING with no stages rather than failing.
    assert response.status_code == 200
    assert response.json()["status"] == "RUNNING"
    assert response.json()["stages"] == []
