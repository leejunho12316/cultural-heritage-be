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
    # Given: the deterministic VCA adapter is running
    # When: its health endpoint is requested
    response = client.get("/health")

    # Then: it declares its deterministic operating mode
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "vca-ai",
        "mode": "deterministic",
    }


def test_assessment_run_when_created(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a Spring-owned assessment identifier
    shared_root = tmp_path / "shared" / "vca"
    input_folder = create_input_folder(shared_root)
    engine_root = tmp_path / "vca_v2"
    engine_root.mkdir()
    stale_project_output = engine_root / "output" / "result" / "artifact-123-run-123"
    stale_project_output.mkdir(parents=True)
    (stale_project_output / "startup.json").write_text("stale", encoding="utf-8")
    sibling_output = engine_root / "output" / "result" / "other-project"
    sibling_output.mkdir(parents=True)
    (sibling_output / "startup.json").write_text("keep", encoding="utf-8")
    captured_command: list[str] = []

    def fake_run(
        command: list[str],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        captured_command.extend(command)
        assert cwd == engine_root
        assert not stale_project_output.exists()
        assert sibling_output.exists()
        assert check is True
        assert capture_output is True
        assert text is True
        assert timeout == 120
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setattr(assessment_runs.subprocess, "run", fake_run)

    # When: Spring creates an internal assessment run
    response = client.post(
        "/internal/vca/assessment-runs",
        json={
            "assessmentId": "artifact-123",
            "projectName": "artifact-123-run-123",
            "inputImageFolder": str(input_folder),
        },
    )

    # Then: the adapter invokes the vca_v2 dry-run entrypoint
    assert response.status_code == 202
    assert response.json() == {
        "runId": "vca-artifact-123",
        "assessmentId": "artifact-123",
        "status": "COMPLETED",
    }
    assert captured_command == [
        "uv",
        "run",
        "python",
        "-m",
        "modules.orchestration.startup",
        "artifact-123-run-123",
        str(input_folder.resolve()),
        "--dry-run",
    ]


def test_assessment_run_when_stage_outputs_are_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: every known vca_v2 stage has stale output for the same project
    shared_root = tmp_path / "shared" / "vca"
    input_folder = create_input_folder(shared_root)
    engine_root = tmp_path / "vca_v2"
    project_name = "artifact-123-run-123"
    sibling_name = "artifact-456-run-456"
    for stage in assessment_runs._ENGINE_OUTPUT_STAGES:
        stale_directory = engine_root / "output" / stage / project_name
        stale_directory.mkdir(parents=True)
        (stale_directory / "stale.txt").write_text("stale", encoding="utf-8")
        sibling_directory = engine_root / "output" / stage / sibling_name
        sibling_directory.mkdir(parents=True)
        (sibling_directory / "keep.txt").write_text("keep", encoding="utf-8")

    def fake_run(
        command: list[str],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        _ = command, cwd, check, capture_output, text, timeout
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setattr(assessment_runs.subprocess, "run", fake_run)

    # When: Spring creates an internal assessment run for that project
    response = client.post(
        "/internal/vca/assessment-runs",
        json={
            "assessmentId": "artifact-123",
            "projectName": project_name,
            "inputImageFolder": str(input_folder),
        },
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
        json={
            "assessmentId": "artifact-123",
            "projectName": "artifact-123-run-123",
            "inputImageFolder": str(outside_folder),
        },
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
        json={
            "assessmentId": "artifact-123",
            "projectName": "artifact-123-run-123",
            "inputImageFolder": str(input_folder),
        },
    )

    # Then: the adapter rejects the folder before invoking vca_v2
    assert response.status_code == 400
    assert "no supported images" in response.json()["detail"]


def test_assessment_status_when_known_run_is_requested() -> None:
    # Given: a deterministic VCA assessment run identifier
    # When: Spring polls the assessment run
    response = client.get("/internal/vca/assessment-runs/vca-artifact-123")

    # Then: the run is immediately complete
    assert response.status_code == 200
    assert response.json() == {
        "runId": "vca-artifact-123",
        "assessmentId": "artifact-123",
        "status": "COMPLETED",
    }


def test_assessment_report_when_known_run_is_requested() -> None:
    # Given: a deterministic VCA assessment run identifier
    # When: Spring retrieves the assessment report
    response = client.get(
        "/internal/vca/assessment-runs/vca-artifact-123/report"
    )

    # Then: it receives a stable placeholder report without model execution
    assert response.status_code == 200
    assert response.json() == {
        "runId": "vca-artifact-123",
        "assessmentId": "artifact-123",
        "status": "COMPLETED",
        "summary": "Deterministic VCA assessment placeholder.",
        "findings": [
            {
                "category": "PLACEHOLDER",
                "severity": "INFO",
                "message": "VCA dry-run completed.",
            }
        ],
    }


def test_assessment_status_when_run_identifier_is_unknown() -> None:
    # Given: a non-VCA run identifier
    # When: Spring polls the assessment run
    response = client.get("/internal/vca/assessment-runs/unknown")

    # Then: the adapter rejects it as an unknown run
    assert response.status_code == 404
