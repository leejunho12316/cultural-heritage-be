from __future__ import annotations

from typing import TYPE_CHECKING, Final, TypeGuard

from modules.anomaly_grouping.startup_runner import (
    run_pre_refinement_grouping_stage,
    run_report_trace_assembly_stage,
)
from modules.orchestration import startup
from modules.orchestration.stage_execution import StartupStageRunners
from modules.prompt_generating.startup_runner import run_prompt_generating_stage
from modules.rag.qwen.qwen_bridge_json import parse_json_object
from modules.rag.startup_runner import run_rag_stage
from modules.report_generating.startup_runner import run_report_generating_stage

if TYPE_CHECKING:
    from pathlib import Path

    from modules.orchestration.stage_execution import ProjectStageRequest
    from modules.report_generating.models import JsonObject, JsonValue

MASK_DRY_RUN_REASON: Final = (
    "skipped during startup dry-run because mask_refining has no dry-run contract"
)
REPORT_TRACE_ASSEMBLY_DRY_RUN_REASON: Final = (
    "skipped during startup dry-run because report_trace_assembly requires "
    "mask_refining outputs"
)
REPORT_GENERATING_DRY_RUN_REASON: Final = (
    "skipped during startup dry-run because report_generating requires "
    "report_trace_assembly outputs"
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
    message = "startup receipt stages must be a list of objects"
    raise AssertionError(message)


def _receipt(workspace_root: Path, project_name: str) -> JsonObject:
    return parse_json_object(
        _receipt_path(workspace_root, project_name).read_text(encoding="utf-8")
    )


def _receipt_path(workspace_root: Path, project_name: str) -> Path:
    return (
        workspace_root
        / "output"
        / "result"
        / project_name
        / "receipts"
        / "startup.json"
    )


def _progress(workspace_root: Path, project_name: str) -> JsonObject:
    progress_path = (
        workspace_root
        / "output"
        / "result"
        / project_name
        / "receipts"
        / "progress.json"
    )
    return parse_json_object(progress_path.read_text(encoding="utf-8"))


def _successful_cli(arguments: tuple[str, ...]) -> int:
    _ = arguments
    return 0


def _successful_project_runner(request: ProjectStageRequest) -> int:
    _ = request
    return 0


def test_startup_reaches_mask_refining_with_injected_stage_runners(
    tmp_path: Path,
) -> None:
    # Given: every middle-stage runner is injected as callable.
    image_root = tmp_path / "inputs"
    _ = _write_image(image_root / "source.jpg")
    mask_calls: list[tuple[str, ...]] = []

    def mask_refining(arguments: tuple[str, ...]) -> int:
        mask_calls.append(arguments)
        return 0

    # When: startup runs through the injected middle-stage project runners.
    exit_code = startup.run(
        ("unwired-middle", str(image_root)),
        workspace_root=tmp_path,
        stage_runners=StartupStageRunners(
            preprocessing=_successful_cli,
            rough_masking=_successful_project_runner,
            visual_cue_generation=_successful_project_runner,
            rag=_successful_project_runner,
            anomaly_grouping=_successful_project_runner,
            prompt_generating=_successful_project_runner,
            mask_refining=mask_refining,
            report_trace_assembly=_successful_project_runner,
            report_generating=_successful_project_runner,
        ),
    )

    # Then: visual cue generation runs before RAG and mask_refining runs.
    assert exit_code == 0
    assert len(mask_calls) == 1
    receipt = _receipt(tmp_path, "unwired-middle")
    assert receipt["status"] == "completed"
    stages = _stage_records(receipt)
    assert stages[1]["name"] == "rough_masking"
    assert stages[1]["status"] == "completed"
    assert stages[2]["name"] == "visual_cue_generation"
    assert stages[2]["status"] == "completed"
    assert stages[3]["name"] == "rag"
    assert stages[3]["status"] == "completed"
    assert stages[4]["name"] == "anomaly_grouping"
    assert stages[4]["status"] == "completed"
    assert stages[5]["name"] == "prompt_generating"
    assert stages[5]["status"] == "completed"
    assert stages[6]["name"] == "mask_refining"
    assert stages[6]["status"] == "completed"
    assert stages[7]["name"] == "report_trace_assembly"
    assert stages[7]["status"] == "completed"
    assert stages[8]["name"] == "report_generating"
    assert stages[8]["status"] == "completed"


def test_startup_local_hash_bypass_reaches_model_backed_stages(
    tmp_path: Path,
) -> None:
    # Given: a local-only run explicitly disables model cache hash verification.
    image_root = tmp_path / "inputs"
    _ = _write_image(image_root / "source.jpg")
    rough_requests: list[ProjectStageRequest] = []
    mask_calls: list[tuple[str, ...]] = []

    def rough_masking(request: ProjectStageRequest) -> int:
        rough_requests.append(request)
        return 0

    def mask_refining(arguments: tuple[str, ...]) -> int:
        mask_calls.append(arguments)
        return 0

    # When: startup runs with the local-only unverified hash bypass flag.
    exit_code = startup.run(
        (
            "local-hash-bypass",
            str(image_root),
            "--allow-unverified-model-hashes-local-only",
        ),
        workspace_root=tmp_path,
        stage_runners=StartupStageRunners(
            preprocessing=_successful_cli,
            rough_masking=rough_masking,
            visual_cue_generation=_successful_project_runner,
            rag=_successful_project_runner,
            anomaly_grouping=_successful_project_runner,
            prompt_generating=_successful_project_runner,
            mask_refining=mask_refining,
            report_trace_assembly=_successful_project_runner,
            report_generating=_successful_project_runner,
        ),
    )

    # Then: both in-process and CLI-backed model stages receive the bypass.
    assert exit_code == 0
    assert len(rough_requests) == 1
    assert not rough_requests[0].verify_model_hashes
    assert len(mask_calls) == 1
    assert "--no-verify-model-hashes" in mask_calls[0]


def test_default_startup_stage_runners_keep_standalone_project_runners() -> None:
    # Given/When: startup runners are constructed with production defaults.
    runners = StartupStageRunners()

    # Then: connected executed stages keep real runners.
    assert runners.rag == run_rag_stage
    assert runners.prompt_generating == run_prompt_generating_stage
    assert runners.anomaly_grouping == run_pre_refinement_grouping_stage
    assert runners.report_trace_assembly == run_report_trace_assembly_stage
    assert runners.report_generating == run_report_generating_stage
    assert (
        runners.visual_cue_generation.__module__
        != "modules.visual_cue_generation.startup_runner"
    )


def test_startup_dry_run_skips_post_mask_real_stages_without_calling_them(
    tmp_path: Path,
) -> None:
    # Given: startup dry-run reaches the mask-refining stage. anomaly_grouping
    # now sits before mask_refining (like rag/prompt_generating), so unlike
    # mask_refining/report_trace_assembly/report_generating it is still
    # *called* during dry-run - it just has to no-op internally in production
    # (this injected double doesn't, so it still records the call).
    image_root = tmp_path / "inputs"
    _ = _write_image(image_root / "source.jpg")
    mask_calls: list[tuple[str, ...]] = []
    project_calls: list[str] = []

    def mask_refining(arguments: tuple[str, ...]) -> int:
        mask_calls.append(arguments)
        return 0

    def pre_mask_runner(request: ProjectStageRequest) -> int:
        project_calls.append(request.stage_name)
        return 0

    def post_mask_runner(request: ProjectStageRequest) -> int:
        project_calls.append(request.stage_name)
        return 0

    # When: startup is run in dry-run mode.
    exit_code = startup.run(
        ("dry-run-mask", str(image_root), "--dry-run"),
        workspace_root=tmp_path,
        stage_runners=StartupStageRunners(
            preprocessing=_successful_cli,
            rough_masking=_successful_project_runner,
            visual_cue_generation=pre_mask_runner,
            anomaly_grouping=pre_mask_runner,
            mask_refining=mask_refining,
            report_trace_assembly=post_mask_runner,
            report_generating=post_mask_runner,
        ),
    )

    # Then: visual_cue_generation/anomaly_grouping can dry-run (both sit
    # before mask_refining), while the real post-mask stages are skipped
    # without ever being called.
    assert exit_code == 0
    assert mask_calls == []
    assert project_calls == ["visual_cue_generation", "anomaly_grouping"]
    stages = _stage_records(_receipt(tmp_path, "dry-run-mask"))
    assert stages[2]["name"] == "visual_cue_generation"
    assert stages[2]["status"] == "completed"
    assert stages[4]["name"] == "anomaly_grouping"
    assert stages[4]["status"] == "completed"
    assert stages[6]["name"] == "mask_refining"
    assert stages[6]["status"] == "skipped"
    assert stages[6]["reason"] == MASK_DRY_RUN_REASON
    assert stages[7]["name"] == "report_trace_assembly"
    assert stages[7]["status"] == "skipped"
    assert stages[7]["reason"] == REPORT_TRACE_ASSEMBLY_DRY_RUN_REASON
    assert stages[8]["name"] == "report_generating"
    assert stages[8]["status"] == "skipped"
    assert stages[8]["reason"] == REPORT_GENERATING_DRY_RUN_REASON


def test_startup_stops_when_visual_cue_generation_fails_before_rag(
    tmp_path: Path,
) -> None:
    # Given: visual cue generation fails before RAG can consume Qwen sidecars.
    image_root = tmp_path / "inputs"
    _ = _write_image(image_root / "source.jpg")
    calls: list[str] = []

    def rag(request: ProjectStageRequest) -> int:
        calls.append(request.stage_name)
        return 0

    def post_mask_runner(request: ProjectStageRequest) -> int:
        calls.append(request.stage_name)
        return 0

    def visual_cue_generation(request: ProjectStageRequest) -> int:
        calls.append(request.stage_name)
        return 9

    # When: startup reaches the visual cue stage.
    exit_code = startup.run(
        ("qwen-not-required", str(image_root)),
        workspace_root=tmp_path,
        stage_runners=StartupStageRunners(
            preprocessing=_successful_cli,
            rough_masking=_successful_project_runner,
            visual_cue_generation=visual_cue_generation,
            rag=rag,
            anomaly_grouping=post_mask_runner,
            prompt_generating=_successful_project_runner,
            mask_refining=_successful_cli,
            report_trace_assembly=post_mask_runner,
            report_generating=post_mask_runner,
        ),
    )

    # Then: startup fails there and does not run RAG or later stages.
    assert exit_code == 9
    assert calls == ["visual_cue_generation"]
    stages = _stage_records(_receipt(tmp_path, "qwen-not-required"))
    assert stages[2]["name"] == "visual_cue_generation"
    assert stages[2]["status"] == "failed"
    assert stages[2]["exit_code"] == 9
    assert [stage["status"] for stage in stages[3:]] == ["skipped"] * 6
    progress = _progress(tmp_path, "qwen-not-required")
    assert progress["status"] == "failed"
    progress_stages = _stage_records(progress)
    assert [stage["status"] for stage in progress_stages] == [
        "completed",
        "completed",
        "failed",
        *(["skipped"] * 6),
    ]


def test_startup_rejects_stage_output_symlink_parent_escape(
    tmp_path: Path,
) -> None:
    # Given: a module output parent resolves outside the workspace.
    image_root = tmp_path / "inputs"
    _ = _write_image(image_root / "source.jpg")
    output_root = tmp_path / "output"
    output_root.mkdir()
    external_root = tmp_path.parent / f"{tmp_path.name}-external-mask-root"
    external_root.mkdir()
    (output_root / "mask_refining").symlink_to(external_root, target_is_directory=True)

    # When: startup validates the project request.
    exit_code = startup.run(
        ("unsafe-stage-root", str(image_root)),
        workspace_root=tmp_path,
        stage_runners=StartupStageRunners(preprocessing=_successful_cli),
    )

    # Then: it fails before writing the startup receipt.
    assert exit_code == 2
    assert not (
        tmp_path
        / "output"
        / "result"
        / "unsafe-stage-root"
        / "receipts"
        / "startup.json"
    ).exists()


def test_startup_rejects_receipt_directory_symlink_escape(
    tmp_path: Path,
) -> None:
    # Given: the receipt directory points outside the workspace.
    image_root = tmp_path / "inputs"
    _ = _write_image(image_root / "source.jpg")
    receipt_dir = _receipt_path(tmp_path, "receipt-dir-escape").parent
    receipt_dir.parent.mkdir(parents=True)
    external_dir = tmp_path.parent / f"{tmp_path.name}-external-receipts"
    external_dir.mkdir()
    receipt_dir.symlink_to(external_dir, target_is_directory=True)

    # When: startup tries to write its final receipt.
    exit_code = startup.run(
        ("receipt-dir-escape", str(image_root), "--dry-run"),
        workspace_root=tmp_path,
        stage_runners=StartupStageRunners(preprocessing=_successful_cli),
    )

    # Then: it fails closed without creating an external receipt.
    assert exit_code == 2
    assert not (external_dir / "startup.json").exists()


def test_startup_rejects_receipt_file_symlink_escape(
    tmp_path: Path,
) -> None:
    # Given: the receipt leaf points at an external file.
    image_root = tmp_path / "inputs"
    _ = _write_image(image_root / "source.jpg")
    receipt_path = _receipt_path(tmp_path, "receipt-file-escape")
    receipt_path.parent.mkdir(parents=True)
    external_file = tmp_path.parent / f"{tmp_path.name}-external-startup.json"
    _ = external_file.write_text("sentinel", encoding="utf-8")
    receipt_path.symlink_to(external_file)

    # When: startup tries to write its final receipt.
    exit_code = startup.run(
        ("receipt-file-escape", str(image_root), "--dry-run"),
        workspace_root=tmp_path,
        stage_runners=StartupStageRunners(preprocessing=_successful_cli),
    )

    # Then: it fails closed without overwriting the external target.
    assert exit_code == 2
    assert external_file.read_text(encoding="utf-8") == "sentinel"
