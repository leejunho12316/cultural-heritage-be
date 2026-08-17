from __future__ import annotations

from typing import TYPE_CHECKING, Final, TypeGuard

import pytest

from modules.orchestration import startup
from modules.orchestration.stage_execution import StartupStageRunners
from modules.rag.qwen.qwen_bridge_json import parse_json_object

if TYPE_CHECKING:
    from pathlib import Path

    from modules.orchestration.stage_execution import ProjectStageRequest
    from modules.report_generating.models import JsonObject, JsonValue


SKIPPED_STAGE_REASON: Final = "no standalone orchestration entrypoint wired yet"
OUT_OF_SCOPE_STAGE_REASON: Final = "outside startup orchestration scope"
ANOMALY_GROUPING_DRY_RUN_REASON: Final = (
    "skipped during startup dry-run because anomaly_grouping requires "
    "mask_refining outputs"
)
REPORT_GENERATING_DRY_RUN_REASON: Final = (
    "skipped during startup dry-run because report_generating requires "
    "anomaly_grouping outputs"
)
STARTUP_RECEIPT_SCHEMA: Final = "vca-startup-receipt-v1"
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
    pytest.fail("startup receipt stages must be a list of objects")


def _module_root(tmp_path: Path, module_name: str, project_name: str) -> Path:
    return tmp_path / "output" / module_name / project_name


def _successful_stage_runner(arguments: tuple[str, ...]) -> int:
    _ = arguments
    return 0


def _successful_project_runner(request: ProjectStageRequest) -> int:
    _ = request
    return 0


def test_startup_invokes_preprocessing_under_project_result_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a project name, an input image folder, and a fake preprocessing seam.
    image_root = tmp_path / "inputs"
    first = _write_image(image_root / "b.png")
    second = _write_image(image_root / "a.jpg")
    _ = (image_root / "notes.txt").write_text("not an image", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def fake_preprocessing(arguments: tuple[str, ...]) -> int:
        calls.append(arguments)
        return 0

    # When: the startup CLI is run in dry-run mode.
    exit_code = startup.run(
        ("artifact-demo", str(image_root), "--dry-run"),
        workspace_root=tmp_path,
        stage_runners=StartupStageRunners(
            preprocessing=fake_preprocessing,
            rough_masking=_successful_project_runner,
            visual_cue_generation=_successful_project_runner,
            rag=_successful_project_runner,
            prompt_generating=_successful_project_runner,
            mask_refining=_successful_stage_runner,
            anomaly_grouping=_successful_project_runner,
            report_generating=_successful_project_runner,
        ),
    )

    # Then: preprocessing receives sorted image paths and the project result root.
    preprocessing_root = _module_root(tmp_path, "preprocessing", "artifact-demo")
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
        (
            "startup: skipped mask_refining "
            "(skipped during startup dry-run because mask_refining has no "
            "dry-run contract)"
        ),
        "startup: starting anomaly_grouping",
        f"startup: skipped anomaly_grouping ({ANOMALY_GROUPING_DRY_RUN_REASON})",
        "startup: starting report_generating",
        f"startup: skipped report_generating ({REPORT_GENERATING_DRY_RUN_REASON})",
    ]
    assert calls == [
        (
            str(second.resolve()),
            str(first.resolve()),
            "--project-name",
            "artifact-demo",
            "--run-root",
            str(preprocessing_root),
            "--dry-run",
        )
    ]
    receipt = parse_json_object(
        (
            tmp_path
            / "output"
            / "result"
            / "artifact-demo"
            / "receipts"
            / "startup.json"
        ).read_text(encoding="utf-8")
    )
    assert receipt["project_name"] == "artifact-demo"
    assert receipt["status"] == "completed"
    assert receipt["output_root"] == str(
        tmp_path / "output" / "result" / "artifact-demo"
    )
    assert receipt["schema"] == STARTUP_RECEIPT_SCHEMA
    stages = _stage_records(receipt)
    assert [stage["name"] for stage in stages] == list(EXPECTED_STAGE_NAMES)
    assert stages[0]["exit_code"] == 0
    assert [stage["status"] for stage in stages[:5]] == ["completed"] * 5
    assert stages[5]["status"] == "skipped"
    assert stages[5]["reason"] == (
        "skipped during startup dry-run because mask_refining has no dry-run contract"
    )
    assert stages[6]["status"] == "skipped"
    assert stages[6]["reason"] == ANOMALY_GROUPING_DRY_RUN_REASON
    assert stages[7]["status"] == "skipped"
    assert stages[7]["reason"] == REPORT_GENERATING_DRY_RUN_REASON


@pytest.mark.parametrize(
    "project_name",
    ["", "..", "../escape", "bad/name", "/abs"],
)
def test_startup_rejects_unsafe_project_names(
    tmp_path: Path, project_name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: an input folder and an unsafe project name.
    image_root = tmp_path / "inputs"
    _ = _write_image(image_root / "source.jpg")

    # When: startup parses the request.
    exit_code = startup.run((project_name, str(image_root)), workspace_root=tmp_path)

    # Then: it rejects before creating an escaped output namespace, and the
    # real contract failure reaches stderr instead of being swallowed silent.
    assert exit_code == 2
    assert not (tmp_path / "output" / "result").exists()
    err = capsys.readouterr().err
    assert "startup: failed startup request:" in err
    assert "ContractValidationError" in err or "PathSafetyError" in err


def test_startup_rejects_empty_input_image_folder(tmp_path: Path) -> None:
    # Given: a valid project name but no input images.
    image_root = tmp_path / "inputs"
    image_root.mkdir()

    # When: startup runs.
    exit_code = startup.run(("empty-project", str(image_root)), workspace_root=tmp_path)

    # Then: it fails before creating a project output directory.
    assert exit_code == 2
    assert not (tmp_path / "output" / "result" / "empty-project").exists()


def test_startup_stops_after_failed_preprocessing(tmp_path: Path) -> None:
    # Given: preprocessing reports a non-zero exit code.
    image_root = tmp_path / "inputs"
    _ = _write_image(image_root / "source.jpg")

    def failed_preprocessing(arguments: tuple[str, ...]) -> int:
        _ = arguments
        return 7

    # When: startup runs.
    exit_code = startup.run(
        ("failed-project", str(image_root)),
        workspace_root=tmp_path,
        preprocessing_runner=failed_preprocessing,
    )

    # Then: the receipt records the failed stage and skipped downstream modules.
    assert exit_code == 7
    receipt = parse_json_object(
        (
            tmp_path
            / "output"
            / "result"
            / "failed-project"
            / "receipts"
            / "startup.json"
        ).read_text(encoding="utf-8")
    )
    assert receipt["status"] == "failed"
    assert receipt["failed_stage"] == "preprocessing"
    stages = _stage_records(receipt)
    assert [stage["name"] for stage in stages] == list(EXPECTED_STAGE_NAMES)
    assert stages[0]["exit_code"] == 7
    assert all(
        stage["status"] == "skipped"
        and stage["reason"] == "skipped because preprocessing failed"
        for stage in stages[1:]
    )


def test_startup_writes_failure_receipt_when_preprocessing_raises(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: preprocessing raises before returning a process exit code.
    image_root = tmp_path / "inputs"
    _ = _write_image(image_root / "source.jpg")

    def broken_preprocessing(arguments: tuple[str, ...]) -> int:
        _ = arguments
        message = "model cache unreadable"
        raise RuntimeError(message)

    # When: startup runs.
    exit_code = startup.run(
        ("raised-project", str(image_root)),
        workspace_root=tmp_path,
        preprocessing_runner=broken_preprocessing,
    )

    # Then: the startup receipt records preprocessing failure without crashing,
    # and the console line carries the real failure reason (not just a class name).
    assert exit_code == 2
    assert capsys.readouterr().out.splitlines() == [
        "startup: starting preprocessing",
        (
            "startup: failed preprocessing (exit_code=2, "
            "reason=preprocessing raised RuntimeError: model cache unreadable)"
        ),
        "startup: skipped rough_masking (skipped because preprocessing failed)",
        "startup: skipped visual_cue_generation (skipped because preprocessing failed)",
        "startup: skipped rag (skipped because preprocessing failed)",
        "startup: skipped prompt_generating (skipped because preprocessing failed)",
        "startup: skipped mask_refining (skipped because preprocessing failed)",
        "startup: skipped anomaly_grouping (skipped because preprocessing failed)",
        "startup: skipped report_generating (skipped because preprocessing failed)",
    ]
    receipt = parse_json_object(
        (
            tmp_path
            / "output"
            / "result"
            / "raised-project"
            / "receipts"
            / "startup.json"
        ).read_text(encoding="utf-8")
    )
    assert receipt["status"] == "failed"
    assert receipt["failed_stage"] == "preprocessing"
    stages = _stage_records(receipt)
    assert stages[0]["status"] == "failed"
    assert stages[0]["exit_code"] == 2
    assert (
        stages[0]["reason"]
        == "preprocessing raised RuntimeError: model cache unreadable"
    )
