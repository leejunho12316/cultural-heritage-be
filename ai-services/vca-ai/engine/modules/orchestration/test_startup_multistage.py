from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Final, TypeGuard

import pytest

from modules.orchestration import startup
from modules.orchestration.stage_execution import StartupStageRunners
from modules.rag.qwen.qwen_bridge_json import parse_json_object

if TYPE_CHECKING:
    from modules.orchestration.stage_execution import ProjectStageRequest
    from modules.report_generating.models import JsonObject, JsonValue

EXPECTED_STAGE_NAMES: Final = (
    "preprocessing",
    "rough_masking",
    "visual_cue_generation",
    "rag",
    "anomaly_grouping",
    "prompt_generating",
    "mask_refining",
    "report_trace_assembly",
    "report_generating",
)
type FailureCase = tuple[str, int, tuple[str, ...], int]


def _write_image(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(b"image")
    return path


def _is_stage_records(value: JsonValue) -> TypeGuard[list[JsonObject]]:
    return isinstance(value, list) and all(isinstance(stage, dict) for stage in value)


def _stage_records(receipt: JsonObject) -> list[JsonObject]:
    stages = receipt["stages"]
    if _is_stage_records(stages):
        return stages
    message = "startup receipt stages must be a list of objects"
    raise AssertionError(message)


def _module_root(tmp_path: Path, module_name: str, project_name: str) -> Path:
    return tmp_path / "output" / module_name / project_name


def test_startup_invokes_post_mask_stages_after_mask_refining_with_module_roots(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: every startup stage has a fake runner.
    image_root = tmp_path / "inputs"
    _ = _write_image(image_root / "source.jpg")
    cli_calls: list[tuple[str, tuple[str, ...]]] = []
    project_calls: list[ProjectStageRequest] = []
    visual_cue_calls: list[ProjectStageRequest] = []

    def cli_runner(stage_name: str) -> startup.PreprocessingRunner:
        def fake(arguments: tuple[str, ...]) -> int:
            cli_calls.append((stage_name, arguments))
            return 0

        return fake

    def project_runner(request: ProjectStageRequest) -> int:
        project_calls.append(request)
        return 0

    def visual_cue_generation(request: ProjectStageRequest) -> int:
        visual_cue_calls.append(request)
        return 0

    # When: startup runs through the connected startup stage set.
    exit_code = startup.run(
        ("connected-project", str(image_root), "--device", "auto"),
        workspace_root=tmp_path,
        stage_runners=StartupStageRunners(
            preprocessing=cli_runner("preprocessing"),
            rough_masking=project_runner,
            visual_cue_generation=visual_cue_generation,
            rag=project_runner,
            anomaly_grouping=project_runner,
            prompt_generating=project_runner,
            mask_refining=cli_runner("mask_refining"),
            report_trace_assembly=project_runner,
            report_generating=project_runner,
        ),
    )

    # Then: visual cues run before RAG, then post-mask stages complete.
    assert exit_code == 0
    assert capsys.readouterr().out.splitlines() == [
        "startup: starting preprocessing",
        "startup: completed preprocessing (exit_code=0)",
        "startup: starting rough_masking",
        "startup: completed rough_masking (exit_code=0)",
        "startup: starting visual_cue_generation",
        "startup: completed visual_cue_generation (exit_code=0)",
        "startup: starting rag",
        "startup: completed rag (exit_code=0)",
        "startup: starting anomaly_grouping",
        "startup: completed anomaly_grouping (exit_code=0)",
        "startup: starting prompt_generating",
        "startup: completed prompt_generating (exit_code=0)",
        "startup: starting mask_refining",
        "startup: completed mask_refining (exit_code=0)",
        "startup: starting report_trace_assembly",
        "startup: completed report_trace_assembly (exit_code=0)",
        "startup: starting report_generating",
        "startup: completed report_generating (exit_code=0)",
    ]
    assert tuple(name for name, _ in cli_calls) == ("preprocessing", "mask_refining")
    assert tuple(request.stage_name for request in project_calls) == (
        "rough_masking", "rag", "anomaly_grouping", "prompt_generating",
        "report_trace_assembly", "report_generating",
    )
    assert tuple(request.stage_name for request in visual_cue_calls) == (
        "visual_cue_generation",
    )
    preprocessing_args = cli_calls[0][1]
    assert "--run-root" in preprocessing_args
    assert preprocessing_args[
        preprocessing_args.index("--run-root") + 1
    ] == str(_module_root(tmp_path, "preprocessing", "connected-project"))
    mask_args = cli_calls[-1][1]
    assert mask_args == (
        "--prompt-output-dir",
        str(_module_root(tmp_path, "prompt_generating", "connected-project")),
        "--rough-root",
        str(_module_root(tmp_path, "rough_masking", "connected-project")),
        "--asset-root",
        str(_module_root(tmp_path, "preprocessing", "connected-project")),
        "--output-dir",
        str(_module_root(tmp_path, "mask_refining", "connected-project")),
        "--model-cache-root",
        str(tmp_path / "models"),
        "--device",
        "auto",
        "--progress-root",
        str(tmp_path / "output" / "result" / "connected-project"),
    )
    receipt = parse_json_object(
        (
            tmp_path
            / "output"
            / "result"
            / "connected-project"
            / "receipts"
            / "startup.json"
        ).read_text(encoding="utf-8")
    )
    stages = _stage_records(receipt)
    assert [stage["name"] for stage in stages] == list(EXPECTED_STAGE_NAMES)
    assert [stage["status"] for stage in stages] == ["completed"] * 9
    assert stages[1]["output_dir"] == str(
        _module_root(tmp_path, "rough_masking", "connected-project")
    )
    assert receipt["output_root"] == str(
        tmp_path / "output" / "result" / "connected-project"
    )
    progress = parse_json_object(
        (
            tmp_path
            / "output"
            / "result"
            / "connected-project"
            / "receipts"
            / "progress.json"
        ).read_text(encoding="utf-8")
    )
    assert progress["status"] == "completed"
    assert progress["current_stage"] is None
    progress_stages = _stage_records(progress)
    assert [stage["name"] for stage in progress_stages] == list(EXPECTED_STAGE_NAMES)
    assert [stage["status"] for stage in progress_stages] == ["completed"] * 9


def test_startup_passes_resolved_preprocessing_device_to_downstream_stages(
    tmp_path: Path,
) -> None:
    # Given: real preprocessing resolves --device auto to mps in its manifest.
    image_root = tmp_path / "inputs"
    _ = _write_image(image_root / "source.jpg")
    project_devices: list[str] = []
    mask_calls: list[tuple[str, ...]] = []

    def preprocessing(arguments: tuple[str, ...]) -> int:
        run_root = Path(arguments[arguments.index("--run-root") + 1])
        manifest_dir = run_root / "manifests"
        manifest_dir.mkdir(parents=True)
        _ = (manifest_dir / "real_preprocessing_manifest.json").write_text(
            '{"device":"mps"}', encoding="utf-8"
        )
        return 0

    def project_runner(request: ProjectStageRequest) -> int:
        project_devices.append(request.device)
        return 0

    def mask_refining(arguments: tuple[str, ...]) -> int:
        mask_calls.append(arguments)
        return 0

    # When: startup runs with auto device selection.
    exit_code = startup.run(
        ("resolved-device-project", str(image_root), "--device", "auto"),
        workspace_root=tmp_path,
        stage_runners=StartupStageRunners(
            preprocessing=preprocessing,
            rough_masking=project_runner,
            visual_cue_generation=project_runner,
            rag=project_runner,
            anomaly_grouping=project_runner,
            prompt_generating=project_runner,
            mask_refining=mask_refining,
            report_trace_assembly=project_runner,
            report_generating=project_runner,
        ),
    )

    # Then: downstream stages receive preprocessing's concrete runtime device.
    assert exit_code == 0
    assert project_devices == ["mps"] * 7
    assert len(mask_calls) == 1
    assert mask_calls[0][mask_calls[0].index("--device") + 1] == "mps"


@pytest.mark.parametrize(
    "failure_case",
    [
        (
            "visual_cue_generation",
            5,
            ("preprocessing", "rough_masking", "visual_cue_generation"),
            2,
        ),
        (
            "rag",
            9,
            ("preprocessing", "rough_masking", "visual_cue_generation", "rag"),
            3,
        ),
        (
            "anomaly_grouping",
            6,
            (
                "preprocessing",
                "rough_masking",
                "visual_cue_generation",
                "rag",
                "anomaly_grouping",
            ),
            4,
        ),
        (
            "report_generating",
            12,
            (
                "preprocessing",
                "rough_masking",
                "visual_cue_generation",
                "rag",
                "anomaly_grouping",
                "prompt_generating",
                "mask_refining",
                "report_trace_assembly",
                "report_generating",
            ),
            8,
        ),
    ],
)
def test_startup_stops_after_failed_project_stage(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    failure_case: FailureCase,
) -> None:
    # Given: one project stage fails after earlier stages complete.
    image_root = tmp_path / "inputs"
    _ = _write_image(image_root / "source.jpg")
    calls: list[str] = []
    failed_stage, failed_exit, expected_calls, failed_index = failure_case

    def successful_cli(stage_name: str) -> startup.PreprocessingRunner:
        def fake(arguments: tuple[str, ...]) -> int:
            _ = arguments
            calls.append(stage_name)
            return 0

        return fake

    def successful_project(request: ProjectStageRequest) -> int:
        calls.append(request.stage_name)
        return failed_exit if request.stage_name == failed_stage else 0

    # When: startup reaches the failing stage.
    exit_code = startup.run(
        ("stage-failure", str(image_root)),
        workspace_root=tmp_path,
        stage_runners=StartupStageRunners(
            preprocessing=successful_cli("preprocessing"),
            rough_masking=successful_project,
            visual_cue_generation=successful_project,
            rag=successful_project,
            anomaly_grouping=successful_project,
            prompt_generating=successful_project,
            mask_refining=successful_cli("mask_refining"),
            report_trace_assembly=successful_project,
            report_generating=successful_project,
        ),
    )

    # Then: later stages are not called and are recorded as skipped.
    assert exit_code == failed_exit
    output_lines = capsys.readouterr().out.splitlines()
    expected_output = [
        line
        for stage_name in expected_calls[:-1]
        for line in (
            f"startup: starting {stage_name}",
            f"startup: completed {stage_name} (exit_code=0)",
        )
    ]
    expected_output.extend(
        (
            f"startup: starting {failed_stage}",
            f"startup: failed {failed_stage} (exit_code={failed_exit})",
        )
    )
    expected_output.extend(
        f"startup: skipped {stage_name} (skipped because {failed_stage} failed)"
        for stage_name in EXPECTED_STAGE_NAMES[failed_index + 1 :]
    )
    assert output_lines == expected_output
    assert tuple(calls) == expected_calls
    receipt = parse_json_object(
        (
            tmp_path
            / "output"
            / "result"
            / "stage-failure"
            / "receipts"
            / "startup.json"
        ).read_text(encoding="utf-8")
    )
    assert receipt["failed_stage"] == failed_stage
    stages = _stage_records(receipt)
    assert stages[failed_index]["status"] == "failed"
    assert stages[failed_index]["exit_code"] == failed_exit
    skipped = stages[failed_index + 1 :]
    assert [stage["status"] for stage in skipped] == ["skipped"] * len(skipped)
    assert all(
        stage["reason"] == f"skipped because {failed_stage} failed"
        for stage in skipped
    )


def test_startup_resumes_from_failed_stage_reusing_prior_completed_stages(
    tmp_path: Path,
) -> None:
    # Given: a first attempt that completes five stages and then fails at
    # mask_refining.
    image_root = tmp_path / "inputs"
    _ = _write_image(image_root / "source.jpg")
    first_calls: list[str] = []

    def failing_cli(stage_name: str, exit_code: int) -> startup.PreprocessingRunner:
        def fake(arguments: tuple[str, ...]) -> int:
            _ = arguments
            first_calls.append(stage_name)
            return exit_code

        return fake

    def successful_project(request: ProjectStageRequest) -> int:
        first_calls.append(request.stage_name)
        return 0

    first_exit_code = startup.run(
        ("resume-project", str(image_root)),
        workspace_root=tmp_path,
        stage_runners=StartupStageRunners(
            preprocessing=failing_cli("preprocessing", 0),
            rough_masking=successful_project,
            visual_cue_generation=successful_project,
            rag=successful_project,
            anomaly_grouping=successful_project,
            prompt_generating=successful_project,
            mask_refining=failing_cli("mask_refining", 1),
            report_trace_assembly=successful_project,
            report_generating=successful_project,
        ),
    )
    assert first_exit_code == 1
    assert first_calls == [
        "preprocessing",
        "rough_masking",
        "visual_cue_generation",
        "rag",
        "anomaly_grouping",
        "prompt_generating",
        "mask_refining",
    ]

    # When: startup is retried with --resume-from-stage mask_refining against
    # the same project root - mirroring how vca-ai would invoke it after
    # copying the prior failed run's completed-stage output (and its
    # startup.json) into a fresh run's directory.
    second_calls: list[str] = []

    def tracked_cli(stage_name: str) -> startup.PreprocessingRunner:
        def fake(arguments: tuple[str, ...]) -> int:
            _ = arguments
            second_calls.append(stage_name)
            return 0

        return fake

    def tracked_project(request: ProjectStageRequest) -> int:
        second_calls.append(request.stage_name)
        return 0

    second_exit_code = startup.run(
        ("resume-project", str(image_root), "--resume-from-stage", "mask_refining"),
        workspace_root=tmp_path,
        stage_runners=StartupStageRunners(
            preprocessing=tracked_cli("preprocessing"),
            rough_masking=tracked_project,
            visual_cue_generation=tracked_project,
            rag=tracked_project,
            anomaly_grouping=tracked_project,
            prompt_generating=tracked_project,
            mask_refining=tracked_cli("mask_refining"),
            report_trace_assembly=tracked_project,
            report_generating=tracked_project,
        ),
    )

    # Then: only the stages from mask_refining onward actually ran, and the
    # final receipt still carries the full nine-stage history.
    assert second_exit_code == 0
    assert second_calls == [
        "mask_refining", "report_trace_assembly", "report_generating",
    ]
    receipt = _run_status_receipt(tmp_path, "resume-project")
    stages = _stage_records(receipt)
    assert [stage["name"] for stage in stages] == list(EXPECTED_STAGE_NAMES)
    assert [stage["status"] for stage in stages] == ["completed"] * 9


def test_startup_resume_fails_closed_without_a_prior_receipt(tmp_path: Path) -> None:
    # Given: no prior run has ever written a startup.json for this project -
    # vca-ai should never send --resume-from-stage in this situation, but the
    # orchestrator must still fail closed rather than silently doing
    # something wrong if it somehow does.
    image_root = tmp_path / "inputs"
    _ = _write_image(image_root / "source.jpg")

    def successful_cli(arguments: tuple[str, ...]) -> int:
        _ = arguments
        return 0

    def successful_project(request: ProjectStageRequest) -> int:
        _ = request
        return 0

    # When: startup is asked to resume from a stage with nothing to resume.
    exit_code = startup.run(
        ("resume-missing", str(image_root), "--resume-from-stage", "mask_refining"),
        workspace_root=tmp_path,
        stage_runners=StartupStageRunners(
            preprocessing=successful_cli,
            rough_masking=successful_project,
            visual_cue_generation=successful_project,
            rag=successful_project,
            anomaly_grouping=successful_project,
            prompt_generating=successful_project,
            mask_refining=successful_cli,
            report_trace_assembly=successful_project,
            report_generating=successful_project,
        ),
    )

    # Then: the run fails closed instead of guessing.
    assert exit_code == 2


def _successful_cli_runner(arguments: tuple[str, ...]) -> int:
    _ = arguments
    return 0


def _write_anomaly_grouping_result(
    paths_root: Path, kept_flags: tuple[bool, ...]
) -> None:
    payload = {
        "candidate_results": [
            {"candidate_id": f"candidate-{index:03d}", "kept": kept}
            for index, kept in enumerate(kept_flags)
        ],
    }
    (paths_root / "anomaly_grouping_result.json").parent.mkdir(
        parents=True, exist_ok=True
    )
    (paths_root / "anomaly_grouping_result.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _run_status_receipt(tmp_path: Path, project_name: str) -> JsonObject:
    return parse_json_object(
        (
            tmp_path
            / "output"
            / "result"
            / project_name
            / "receipts"
            / "startup.json"
        ).read_text(encoding="utf-8")
    )


def test_all_stages_complete_with_kept_candidates_reports_success(
    tmp_path: Path,
) -> None:
    # Given: every stage completes and report_trace_assembly keeps one
    # candidate (the stage that now determines the final kept-candidate
    # count, writing into report_generating's directory - see
    # run_report_trace_assembly_stage).
    image_root = tmp_path / "inputs"
    _ = _write_image(image_root / "source.jpg")

    def report_trace_assembly(request: ProjectStageRequest) -> int:
        _write_anomaly_grouping_result(request.paths.report_generating, (True,))
        return 0

    def project_runner(request: ProjectStageRequest) -> int:
        _ = request
        return 0

    # When: startup runs the full connected stage set.
    exit_code = startup.run(
        ("run-status-success", str(image_root)),
        workspace_root=tmp_path,
        stage_runners=StartupStageRunners(
            preprocessing=_successful_cli_runner,
            rough_masking=project_runner,
            visual_cue_generation=project_runner,
            rag=project_runner,
            anomaly_grouping=project_runner,
            prompt_generating=project_runner,
            mask_refining=_successful_cli_runner,
            report_trace_assembly=report_trace_assembly,
            report_generating=project_runner,
        ),
    )

    # Then: the receipt reports a full success.
    assert exit_code == 0
    receipt = _run_status_receipt(tmp_path, "run-status-success")
    assert receipt["run_status"] == "success"
    assert receipt["final_success"] is True


def test_all_stages_complete_with_zero_kept_candidates_reports_no_valid_targets(
    tmp_path: Path,
) -> None:
    # Given: every stage completes but report_trace_assembly keeps nothing.
    image_root = tmp_path / "inputs"
    _ = _write_image(image_root / "source.jpg")

    def report_trace_assembly(request: ProjectStageRequest) -> int:
        _write_anomaly_grouping_result(
            request.paths.report_generating, (False, False)
        )
        return 0

    def project_runner(request: ProjectStageRequest) -> int:
        _ = request
        return 0

    # When: startup runs the full connected stage set.
    exit_code = startup.run(
        ("run-status-no-targets", str(image_root)),
        workspace_root=tmp_path,
        stage_runners=StartupStageRunners(
            preprocessing=_successful_cli_runner,
            rough_masking=project_runner,
            visual_cue_generation=project_runner,
            rag=project_runner,
            anomaly_grouping=project_runner,
            prompt_generating=project_runner,
            mask_refining=_successful_cli_runner,
            report_trace_assembly=report_trace_assembly,
            report_generating=project_runner,
        ),
    )

    # Then: the run completed cleanly but found nothing worth keeping.
    assert exit_code == 0
    receipt = _run_status_receipt(tmp_path, "run-status-no-targets")
    assert receipt["run_status"] == "no_valid_rough_targets"
    assert receipt["final_success"] is False


def test_rough_masking_budget_block_reports_blocked(tmp_path: Path) -> None:
    # Given: rough_masking fails after writing a tile-budget approval request.
    image_root = tmp_path / "inputs"
    _ = _write_image(image_root / "source.jpg")

    def rough_masking(request: ProjectStageRequest) -> int:
        request.paths.rough_masking.mkdir(parents=True, exist_ok=True)
        (request.paths.rough_masking / "budget_approval_request.json").write_text(
            "{}", encoding="utf-8"
        )
        return 2

    # When: startup reaches the blocked rough_masking stage.
    exit_code = startup.run(
        ("run-status-blocked", str(image_root)),
        workspace_root=tmp_path,
        stage_runners=StartupStageRunners(
            preprocessing=_successful_cli_runner,
            rough_masking=rough_masking,
            visual_cue_generation=_never_project_runner,
            rag=_never_project_runner,
            anomaly_grouping=_never_project_runner,
            prompt_generating=_never_project_runner,
            mask_refining=_never_stage_runner,
            report_trace_assembly=_never_project_runner,
            report_generating=_never_project_runner,
        ),
    )

    # Then: the receipt distinguishes a budget block from a generic failure.
    assert exit_code == 2
    receipt = _run_status_receipt(tmp_path, "run-status-blocked")
    assert receipt["run_status"] == "blocked"
    assert receipt["final_success"] is False


def test_non_rough_masking_stage_failure_reports_failure(tmp_path: Path) -> None:
    # Given: mask_refining fails without leaving any budget-approval artifact.
    # anomaly_grouping now sits *before* mask_refining, so by the time
    # mask_refining fails it has already run successfully -
    # report_trace_assembly (after mask_refining) is the one that must not run.
    image_root = tmp_path / "inputs"
    _ = _write_image(image_root / "source.jpg")

    def mask_refining(arguments: tuple[str, ...]) -> int:
        _ = arguments
        return 2

    def project_runner(request: ProjectStageRequest) -> int:
        _ = request
        return 0

    # When: startup reaches the failing mask_refining stage.
    exit_code = startup.run(
        ("run-status-failure", str(image_root)),
        workspace_root=tmp_path,
        stage_runners=StartupStageRunners(
            preprocessing=_successful_cli_runner,
            rough_masking=project_runner,
            visual_cue_generation=project_runner,
            rag=project_runner,
            anomaly_grouping=project_runner,
            prompt_generating=project_runner,
            mask_refining=mask_refining,
            report_trace_assembly=_never_project_runner,
            report_generating=_never_project_runner,
        ),
    )

    # Then: the receipt reports a generic hard failure.
    assert exit_code == 2
    receipt = _run_status_receipt(tmp_path, "run-status-failure")
    assert receipt["run_status"] == "failure"
    assert receipt["final_success"] is False


def test_report_generating_only_failure_reports_incomplete(tmp_path: Path) -> None:
    # Given: every upstream stage completes with a kept candidate, but
    # report_generating itself fails its own verification.
    image_root = tmp_path / "inputs"
    _ = _write_image(image_root / "source.jpg")

    def report_trace_assembly(request: ProjectStageRequest) -> int:
        _write_anomaly_grouping_result(request.paths.report_generating, (True,))
        return 0

    def report_generating(request: ProjectStageRequest) -> int:
        _ = request
        return 2

    def project_runner(request: ProjectStageRequest) -> int:
        _ = request
        return 0

    # When: startup reaches the failing report_generating stage.
    exit_code = startup.run(
        ("run-status-incomplete", str(image_root)),
        workspace_root=tmp_path,
        stage_runners=StartupStageRunners(
            preprocessing=_successful_cli_runner,
            rough_masking=project_runner,
            visual_cue_generation=project_runner,
            rag=project_runner,
            anomaly_grouping=project_runner,
            prompt_generating=project_runner,
            mask_refining=_successful_cli_runner,
            report_trace_assembly=report_trace_assembly,
            report_generating=report_generating,
        ),
    )

    # Then: real candidate data exists, so this is incomplete, not a failure.
    assert exit_code == 2
    receipt = _run_status_receipt(tmp_path, "run-status-incomplete")
    assert receipt["run_status"] == "incomplete"
    assert receipt["final_success"] is False


def test_dry_run_reports_incomplete_pre_qwen_preview(tmp_path: Path) -> None:
    # Given: startup runs in dry-run mode.
    image_root = tmp_path / "inputs"
    _ = _write_image(image_root / "source.jpg")

    def project_runner(request: ProjectStageRequest) -> int:
        _ = request
        return 0

    # When: the startup CLI is run with --dry-run.
    exit_code = startup.run(
        ("run-status-dry-run", str(image_root), "--dry-run"),
        workspace_root=tmp_path,
        stage_runners=StartupStageRunners(
            preprocessing=_successful_cli_runner,
            rough_masking=project_runner,
            visual_cue_generation=project_runner,
            rag=project_runner,
            anomaly_grouping=project_runner,
            prompt_generating=project_runner,
            mask_refining=_successful_cli_runner,
            report_trace_assembly=project_runner,
            report_generating=project_runner,
        ),
    )

    # Then: the receipt reports the dry-run preview status with an OK exit code.
    assert exit_code == 0
    receipt = _run_status_receipt(tmp_path, "run-status-dry-run")
    assert receipt["run_status"] == "incomplete_pre_qwen_preview"
    assert receipt["final_success"] is False


def _never_project_runner(request: ProjectStageRequest) -> int:
    pytest.fail(f"{request.stage_name} must not run after rough_masking is blocked")


def _never_stage_runner(arguments: tuple[str, ...]) -> int:
    _ = arguments
    pytest.fail("cli stage runner must not run after rough_masking is blocked")
