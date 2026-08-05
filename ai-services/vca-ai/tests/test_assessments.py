import subprocess
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.services import assessment_runs


client = TestClient(app)


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
        command: list[str], *, cwd: Path, check: bool, capture_output: bool, text: bool, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        captured_command.extend(command)
        assert cwd == engine_root
        assert not stale_output.exists()
        assert (check, capture_output, text, timeout) == (True, True, True, 600)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setenv("VCA_RUN_TIMEOUT_SECONDS", "600")
    monkeypatch.setenv("VCA_DRY_RUN_TIMEOUT_SECONDS", "120")
    monkeypatch.setattr(assessment_runs.subprocess, "run", fake_run)

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
        command: list[str], *, cwd: Path, check: bool, capture_output: bool, text: bool, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        _ = cwd, check, capture_output, text, timeout
        captured_command.extend(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setenv("VCA_DEVICE", "cpu")
    monkeypatch.setenv("VCA_MAX_IMAGES", "2")
    monkeypatch.setenv("VCA_MODEL_CACHE_ROOT", "/opt/vca-models/models")
    monkeypatch.setattr(assessment_runs.subprocess, "run", fake_run)

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
        command: list[str], *, cwd: Path, check: bool, capture_output: bool, text: bool, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        _ = cwd, check, capture_output, text, timeout
        captured_command.extend(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setenv("VCA_RUN_MODE", "dry-run")
    monkeypatch.setenv("VCA_DEVICE", "cuda")
    monkeypatch.setenv("VCA_MAX_IMAGES", "2")
    monkeypatch.setattr(assessment_runs.subprocess, "run", fake_run)

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
        command: list[str], *, cwd: Path, check: bool, capture_output: bool, text: bool, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        _ = cwd, check, capture_output, text, timeout
        captured_command.extend(command)
        raise subprocess.CalledProcessError(2, command, output="", stderr="GPU unavailable")

    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setattr(assessment_runs.subprocess, "run", fake_run)

    # When: Spring creates a real assessment run
    response = client.post(
        "/internal/vca/assessment-runs",
        json={"assessmentId": "artifact-123", "projectName": "artifact-123-run-123", "inputImageFolder": str(input_folder)},
    )

    # Then: the non-zero process exit remains a gateway failure without dry-run fallback
    assert response.status_code == 502
    assert "GPU unavailable" in response.json()["detail"]
    assert "--dry-run" not in captured_command


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
    for stage in assessment_runs._ENGINE_OUTPUT_STAGES:
        for name, content in ((project_name, "stale"), (sibling_name, "keep")):
            output_directory = engine_root / "output" / stage / name
            output_directory.mkdir(parents=True)
            (output_directory / "output.txt").write_text(content, encoding="utf-8")

    def fake_run(
        command: list[str], *, cwd: Path, check: bool, capture_output: bool, text: bool, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        _ = cwd, check, capture_output, text, timeout
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setattr(assessment_runs.subprocess, "run", fake_run)

    # When: Spring creates an internal assessment run for that project
    response = client.post(
        "/internal/vca/assessment-runs",
        json={"assessmentId": "artifact-123", "projectName": project_name, "inputImageFolder": str(input_folder)},
    )

    # Then: only the current project's stale output directories are cleared
    assert response.status_code == 202
    for stage in assessment_runs._ENGINE_OUTPUT_STAGES:
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
