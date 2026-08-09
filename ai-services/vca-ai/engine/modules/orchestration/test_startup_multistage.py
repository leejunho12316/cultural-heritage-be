from __future__ import annotations

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
    "prompt_generating",
    "mask_refining",
    "anomaly_grouping",
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
            prompt_generating=project_runner,
            mask_refining=cli_runner("mask_refining"),
            anomaly_grouping=project_runner,
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
        "startup: starting prompt_generating",
        "startup: completed prompt_generating (exit_code=0)",
        "startup: starting mask_refining",
        "startup: completed mask_refining (exit_code=0)",
        "startup: starting anomaly_grouping",
        "startup: completed anomaly_grouping (exit_code=0)",
        "startup: starting report_generating",
        "startup: completed report_generating (exit_code=0)",
    ]
    assert tuple(name for name, _ in cli_calls) == ("preprocessing", "mask_refining")
    assert tuple(request.stage_name for request in project_calls) == (
        "rough_masking", "rag", "prompt_generating",
        "anomaly_grouping", "report_generating",
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
    assert [stage["status"] for stage in stages] == ["completed"] * 8
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
    assert [stage["status"] for stage in progress_stages] == ["completed"] * 8


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
            prompt_generating=project_runner,
            mask_refining=mask_refining,
            anomaly_grouping=project_runner,
            report_generating=project_runner,
        ),
    )

    # Then: downstream stages receive preprocessing's concrete runtime device.
    assert exit_code == 0
    assert project_devices == ["mps", "mps", "mps", "mps", "mps", "mps"]
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
                "prompt_generating",
                "mask_refining",
                "anomaly_grouping",
            ),
            6,
        ),
        (
            "report_generating",
            12,
            (
                "preprocessing",
                "rough_masking",
                "visual_cue_generation",
                "rag",
                "prompt_generating",
                "mask_refining",
                "anomaly_grouping",
                "report_generating",
            ),
            7,
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
            prompt_generating=successful_project,
            mask_refining=successful_cli("mask_refining"),
            anomaly_grouping=successful_project,
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
