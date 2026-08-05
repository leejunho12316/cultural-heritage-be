import json
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
        grouping_path = (
            engine_root
            / "output"
            / "anomaly_grouping"
            / project_name
            / "anomaly_grouping_result.json"
        )
        grouping_path.parent.mkdir(parents=True)
        grouping_path.write_text(
            json.dumps(
                {
                    "candidate_results": [
                        {"candidate_id": candidate_id, "kept": kept}
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
        command: list[str], *, cwd: Path, check: bool, capture_output: bool, text: bool, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        _ = cwd, check, capture_output, text, timeout
        write_report_artifacts(
            engine_root,
            project_name,
            (("candidate-kept", True), ("candidate-dropped", False)),
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setattr(assessment_runs.subprocess, "run", fake_run)

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
            "message": "Retained VCA anomaly candidate candidate-kept.",
        }
    ]


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
        command: list[str], *, cwd: Path, check: bool, capture_output: bool, text: bool, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        _ = cwd, check, capture_output, text, timeout
        write_report_artifacts(engine_root, project_name, ())
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("VCA_SHARED_STORAGE_ROOT", str(shared_root))
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))
    monkeypatch.setattr(assessment_runs.subprocess, "run", fake_run)

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
