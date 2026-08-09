from __future__ import annotations

# ruff: noqa: E402, I001

import sys
from types import ModuleType
from typing import TYPE_CHECKING

_ = sys.modules.setdefault("torch", ModuleType("torch"))

from modules.orchestration.stage_execution import ProjectStageRequest
from modules.orchestration.stage_paths import StagePathMap
from modules.report_generating import startup_runner
from modules.report_generating.models import ReportGeneratingRequest

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from modules.report_generating.models import JsonObject


def test_startup_runner_succeeds_when_report_generation_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the standalone report generator reports its documented pass status.
    request = ProjectStageRequest(
        "project-a",
        "report_generating",
        _paths(tmp_path),
        "cpu",
        tmp_path / "models",
        dry_run=False,
        verify_model_hashes=True,
    )

    def report_generation(_: ReportGeneratingRequest) -> JsonObject:
        return {
            "schema": "report_generating_result_v1",
            "verification_status": "pass",
            "trace_root": str(tmp_path / "report"),
            "final_root": str(tmp_path / "final_report"),
        }

    monkeypatch.setattr(startup_runner, "run_report_generation", report_generation)

    # When: the project-stage adapter delegates to report generation.
    exit_code = startup_runner.run_report_generating_stage(request)

    # Then: the documented success value reaches startup as a successful exit code.
    assert exit_code == 0


def test_startup_runner_passes_anomaly_grouping_trace_source_to_report_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a project-stage request points at the anomaly grouping handoff sidecar.
    request = ProjectStageRequest(
        "project-a",
        "report_generating",
        _paths(tmp_path),
        "cpu",
        tmp_path / "models",
        dry_run=False,
        verify_model_hashes=True,
    )
    received: list[ReportGeneratingRequest] = []

    def report_generation(report_request: ReportGeneratingRequest) -> JsonObject:
        received.append(report_request)
        return {
            "schema": "report_generating_result_v1",
            "verification_status": "pass",
            "trace_root": str(tmp_path / "report"),
            "final_root": str(tmp_path / "final_report"),
        }

    monkeypatch.setattr(startup_runner, "run_report_generation", report_generation)

    # When: startup delegates report generation.
    exit_code = startup_runner.run_report_generating_stage(request)

    # Then: the report stage consumes anomaly_grouping's report_trace_source.json.
    assert exit_code == 0
    assert received == [
        ReportGeneratingRequest(
            tmp_path,
            tmp_path / "output" / "report_generating" / "project-a",
            tmp_path
            / "output"
            / "anomaly_grouping"
            / "project-a"
            / "report_trace_source.json",
        )
    ]


def test_startup_runner_returns_two_when_report_verification_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: report generation completes but static verification rejects publication.
    request = ProjectStageRequest(
        "project-a",
        "report_generating",
        _paths(tmp_path),
        "cpu",
        tmp_path / "models",
        dry_run=False,
        verify_model_hashes=True,
    )

    def report_generation(_: ReportGeneratingRequest) -> JsonObject:
        return {
            "schema": "report_generating_result_v1",
            "verification_status": "fail",
            "trace_root": str(tmp_path / "report"),
            "final_root": str(tmp_path / "final_report"),
        }

    monkeypatch.setattr(startup_runner, "run_report_generation", report_generation)

    # When: the project-stage adapter delegates to report generation.
    exit_code = startup_runner.run_report_generating_stage(request)

    # Then: startup receives a failed stage exit code.
    assert exit_code == 2


def _paths(root: Path) -> StagePathMap:
    project_name = "project-a"
    return StagePathMap(
        root / "output" / "preprocessing" / project_name,
        root / "output" / "rough_masking" / project_name,
        root / "output" / "visual_cue_generation" / project_name,
        root / "output" / "rag" / project_name,
        root / "output" / "prompt_generating" / project_name,
        root / "output" / "mask_refining" / project_name,
        root / "output" / "anomaly_grouping" / project_name,
        root / "output" / "report_generating" / project_name,
    )
