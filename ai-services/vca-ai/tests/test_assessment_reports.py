import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.services import assessment_runs


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
        run: object, input_directory: object, settings: object
    ) -> None:
        assessment_runs._run_vca_and_record_failure(run, input_directory, settings)

    monkeypatch.setattr(assessment_runs, "_launch_background", launch_inline)


def create_input_folder(shared_root: Path) -> Path:
    input_folder = shared_root / "run-123" / "input"
    input_folder.mkdir(parents=True)
    (input_folder / "front.jpg").write_bytes(b"vca image bytes")
    return input_folder


def write_report_artifacts(
    engine_root: Path,
    project_name: str,
    candidate_results: tuple[tuple[str, bool], ...] | None = None,
    *,
    final_success: bool = True,
    verification_status: str = "pass",
) -> None:
    startup_path = engine_root / "output" / "result" / project_name / "receipts" / "startup.json"
    final_report_root = (
        engine_root / "output" / "report_generating" / project_name / "final_report"
    )
    startup_path.parent.mkdir(parents=True)
    final_report_root.joinpath("verification").mkdir(parents=True)
    startup_path.write_text(
        json.dumps({"project_name": project_name, "status": "completed", "stages": []}),
        encoding="utf-8",
    )
    final_report_root.joinpath("metadata.json").write_text(
        json.dumps({"schema": "raw-image-final-report-v1", "final_success": final_success}),
        encoding="utf-8",
    )
    final_report_root.joinpath("verification", "receipt.json").write_text(
        json.dumps(
            {
                "schema": "raw-image-final-report-static-v1",
                "verification_status": verification_status,
            }
        ),
        encoding="utf-8",
    )
    if candidate_results is not None:
        report_metadata_path = (
            engine_root
            / "output"
            / "report_generating"
            / project_name
            / "report"
            / "metadata.json"
        )
        report_metadata_path.parent.mkdir(parents=True)
        report_metadata_path.write_text(
            json.dumps(
                {
                    "candidates": [
                        {
                            "candidate_id": candidate_id,
                            "image_id": "image-001",
                            "concept_family": "crack",
                            "hybrid_descriptor": "line surface",
                            "terminal_status": "kept" if kept else "suppressed",
                            "citations": [],
                            "bbox": {
                                "x_min": 10.0,
                                "y_min": 20.0,
                                "x_max": 30.0,
                                "y_max": 40.0,
                            },
                        }
                        for candidate_id, kept in candidate_results
                    ]
                }
            ),
            encoding="utf-8",
        )


def write_dry_run_artifacts(
    engine_root: Path,
    project_name: str,
    *,
    include_preprocessing_evidence: bool = True,
) -> None:
    startup_path = engine_root / "output" / "result" / project_name / "receipts" / "startup.json"
    startup_path.parent.mkdir(parents=True)
    startup_path.write_text(
        json.dumps(
            {
                "project_name": project_name,
                "status": "completed",
                "stages": [
                    {"name": "preprocessing", "status": "completed"},
                    {"name": "rough_masking", "status": "completed"},
                    {"name": "visual_cue_generation", "status": "completed"},
                    {"name": "rag", "status": "completed"},
                    {"name": "prompt_generating", "status": "completed"},
                    {
                        "name": "mask_refining",
                        "status": "skipped",
                        "reason": "skipped during startup dry-run because mask_refining has no dry-run contract",
                    },
                    {
                        "name": "anomaly_grouping",
                        "status": "skipped",
                        "reason": "skipped during startup dry-run because anomaly_grouping requires mask_refining outputs",
                    },
                    {
                        "name": "report_generating",
                        "status": "skipped",
                        "reason": "skipped during startup dry-run because report_generating requires anomaly_grouping outputs",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    if include_preprocessing_evidence:
        manifest_path = (
            engine_root
            / "output"
            / "preprocessing"
            / project_name
            / "manifests"
            / "real_preprocessing_manifest.json"
        )
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "device": "not_executed_dry_run",
                    "detector_lane_status": "dry_run_not_executed",
                    "model_invocations": 0,
                    "sam2_calls": 0,
                }
            ),
            encoding="utf-8",
        )


def test_assessment_report_when_kept_candidates_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a completed VCA run with final-report and anomaly artifacts
    shared_root = tmp_path / "shared" / "vca"
    input_folder = create_input_folder(shared_root)
    engine_root = tmp_path / "vca_v2"
    project_name = "artifact-123-run-123"

    def fake_run(
        command: list[str], *, cwd: Path, timeout: int, run_id: str
    ) -> subprocess.CompletedProcess[str]:
        _ = cwd, timeout
        write_report_artifacts(
            engine_root,
            project_name,
            (("candidate-kept", True), ("candidate-dropped", False)),
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setattr(assessment_runs, "_run_command", fake_run)

    # When: Spring creates the run and retrieves its report
    created = client.post(
        "/internal/vca/assessment-runs",
        json={"assessmentId": "artifact-123", "projectName": project_name, "inputImageFolder": str(input_folder)},
    )
    response = client.get(f"/internal/vca/assessment-runs/{created.json()['runId']}/report")

    # Then: the report is derived from generated artifacts rather than placeholders
    assert created.status_code == 202
    assert response.status_code == 200
    assert response.json()["summary"] == "VCA report for artifact-123-run-123: verification pass."
    assert response.json()["findings"] == [
        {
            "category": "VCA_ANOMALY",
            "severity": "INFO",
            "message": "crack: line surface",
            "candidateId": "candidate-kept",
            "imageId": "image-001",
            "conceptFamily": "crack",
            "descriptor": "line surface",
            "citations": [],
            "bbox": {"xMin": 10.0, "yMin": 20.0, "xMax": 30.0, "yMax": 40.0},
            "polygons": None,
        }
    ]


def write_input_manifest(
    engine_root: Path,
    project_name: str,
    images: tuple[tuple[str, str], ...],
) -> None:
    manifest_path = (
        engine_root / "output" / "preprocessing" / project_name / "manifests" / "input_manifest.json"
    )
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "images": [
                    {"image_id": image_id, "file_sha256": file_sha256}
                    for image_id, file_sha256 in images
                ]
            }
        ),
        encoding="utf-8",
    )


def test_assessment_report_translates_engine_image_id_to_uploaded_file_sha256(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a completed run whose preprocessing manifest records which
    # uploaded file's content sha256 the engine's own image_id refers to.
    shared_root = tmp_path / "shared" / "vca"
    input_folder = create_input_folder(shared_root)
    engine_root = tmp_path / "vca_v2"
    project_name = "artifact-123-run-123"
    uploaded_file_sha256 = "a" * 64

    def fake_run(
        command: list[str], *, cwd: Path, timeout: int, run_id: str
    ) -> subprocess.CompletedProcess[str]:
        _ = cwd, timeout
        write_report_artifacts(engine_root, project_name, (("candidate-kept", True),))
        write_input_manifest(engine_root, project_name, (("image-001", uploaded_file_sha256),))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setattr(assessment_runs, "_run_command", fake_run)

    # When: Spring creates the run and retrieves its report.
    created = client.post(
        "/internal/vca/assessment-runs",
        json={"assessmentId": "artifact-123", "projectName": project_name, "inputImageFolder": str(input_folder)},
    )
    response = client.get(f"/internal/vca/assessment-runs/{created.json()['runId']}/report")

    # Then: the finding reports the uploaded file's sha256, not the engine's
    # own opaque image_id - Spring/FE only ever know the sha256, so a raw
    # engine image_id could never be matched back to an uploaded image.
    assert response.status_code == 200
    assert response.json()["findings"][0]["imageId"] == uploaded_file_sha256


def test_assessment_report_keeps_engine_image_id_when_manifest_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a completed run with no preprocessing manifest available
    # (e.g. cleaned up, or an older run predating this translation).
    shared_root = tmp_path / "shared" / "vca"
    input_folder = create_input_folder(shared_root)
    engine_root = tmp_path / "vca_v2"
    project_name = "artifact-123-run-123"

    def fake_run(
        command: list[str], *, cwd: Path, timeout: int, run_id: str
    ) -> subprocess.CompletedProcess[str]:
        _ = cwd, timeout
        write_report_artifacts(engine_root, project_name, (("candidate-kept", True),))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setattr(assessment_runs, "_run_command", fake_run)

    # When: Spring creates the run and retrieves its report.
    created = client.post(
        "/internal/vca/assessment-runs",
        json={"assessmentId": "artifact-123", "projectName": project_name, "inputImageFolder": str(input_folder)},
    )
    response = client.get(f"/internal/vca/assessment-runs/{created.json()['runId']}/report")

    # Then: translation degrades gracefully to the engine's raw image_id
    # instead of failing the whole report.
    assert response.status_code == 200
    assert response.json()["findings"][0]["imageId"] == "image-001"


def test_assessment_report_when_no_anomaly_candidates_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a completed VCA run with reports but no kept anomaly candidates
    shared_root = tmp_path / "shared" / "vca"
    input_folder = create_input_folder(shared_root)
    engine_root = tmp_path / "vca_v2"
    project_name = "artifact-123-run-123"

    def fake_run(
        command: list[str], *, cwd: Path, timeout: int, run_id: str
    ) -> subprocess.CompletedProcess[str]:
        _ = cwd, timeout
        write_report_artifacts(engine_root, project_name, ())
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setattr(assessment_runs, "_run_command", fake_run)

    # When: Spring retrieves the completed run's report
    created = client.post(
        "/internal/vca/assessment-runs",
        json={"assessmentId": "artifact-123", "projectName": project_name, "inputImageFolder": str(input_folder)},
    )
    response = client.get(f"/internal/vca/assessment-runs/{created.json()['runId']}/report")

    # Then: availability of the generated report is surfaced as a finding
    assert created.status_code == 202
    assert response.status_code == 200
    assert response.json()["findings"] == [
        {
            "category": "VCA_REPORT",
            "severity": "INFO",
            "message": "Generated VCA report is available for artifact-123-run-123.",
            "candidateId": None,
            "imageId": None,
            "conceptFamily": None,
            "descriptor": None,
            "citations": [],
            "bbox": None,
            "polygons": None,
        }
    ]


def test_assessment_report_when_real_mode_only_has_dry_run_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: default real mode and completed dry-run artifacts without a final report
    engine_root = tmp_path / "vca_v2"
    project_name = "artifact-123-run-123"
    write_dry_run_artifacts(engine_root, project_name)
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))

    # When: Spring requests the report without opting into dry-run mode
    response = client.get(f"/internal/vca/assessment-runs/vca-artifact-123~{project_name}/report")

    # Then: final report artifacts remain mandatory and no synthetic report is returned
    assert response.status_code == 502
    assert "final report metadata" in response.json()["detail"]


def test_assessment_report_when_final_metadata_is_not_successful(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a real final report whose metadata says the run is not successful
    engine_root = tmp_path / "vca_v2"
    project_name = "artifact-123-run-123"
    write_report_artifacts(engine_root, project_name, final_success=False)
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))

    # When: Spring requests the real assessment report
    response = client.get(f"/internal/vca/assessment-runs/vca-artifact-123~{project_name}/report")

    # Then: the adapter fails closed instead of serving a partial report as complete
    assert response.status_code == 502
    assert "metadata did not mark the run successful" in response.json()["detail"]


def test_assessment_report_when_final_verification_did_not_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a real final report whose static verification failed
    engine_root = tmp_path / "vca_v2"
    project_name = "artifact-123-run-123"
    write_report_artifacts(engine_root, project_name, verification_status="fail")
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))

    # When: Spring requests the real assessment report
    response = client.get(f"/internal/vca/assessment-runs/vca-artifact-123~{project_name}/report")

    # Then: failed verification is not exposed as a completed report
    assert response.status_code == 502
    assert "verification did not pass" in response.json()["detail"]


def test_assessment_report_when_dry_run_evidence_is_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: explicit dry-run mode and startup artifacts proving its completed plan
    engine_root = tmp_path / "vca_v2"
    project_name = "artifact-123-run-123"
    write_dry_run_artifacts(engine_root, project_name)
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setenv("VCA_RUN_MODE", "dry-run")

    # When: Spring requests the dry-run assessment report
    response = client.get(f"/internal/vca/assessment-runs/vca-artifact-123~{project_name}/report")

    # Then: it receives an explicit synthetic report rather than a final-report placeholder
    assert response.status_code == 200
    assert response.json()["summary"] == (
        "VCA dry-run completed for artifact-123-run-123; "
        "final report generation was intentionally skipped."
    )
    assert response.json()["findings"] == [
        {
            "category": "VCA_REPORT",
            "severity": "INFO",
            "message": "Dry-run evidence verified; no final VCA report was generated for artifact-123-run-123.",
            "candidateId": None,
            "imageId": None,
            "conceptFamily": None,
            "descriptor": None,
            "citations": [],
            "bbox": None,
            "polygons": None,
        }
    ]


def test_assessment_report_when_dry_run_evidence_is_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: dry-run mode with a completed receipt but no model-free preprocessing evidence
    engine_root = tmp_path / "vca_v2"
    project_name = "artifact-123-run-123"
    write_dry_run_artifacts(
        engine_root,
        project_name,
        include_preprocessing_evidence=False,
    )
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setenv("VCA_RUN_MODE", "dry-run")

    # When: Spring requests the report
    response = client.get(f"/internal/vca/assessment-runs/vca-artifact-123~{project_name}/report")

    # Then: absent final reports alone cannot create a synthetic dry-run report
    assert response.status_code == 502
    assert "dry-run preprocessing evidence" in response.json()["detail"]
