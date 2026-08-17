from __future__ import annotations

from typing import TYPE_CHECKING

from modules.orchestration import startup
from modules.orchestration.stage_execution import StartupStageRunners

if TYPE_CHECKING:
    from pathlib import Path

    from modules.orchestration.stage_execution import ProjectStageRequest


def _write_image(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(b"image")
    return path


def _successful_project_runner(request: ProjectStageRequest) -> int:
    _ = request
    return 0


def _successful_cli(arguments: tuple[str, ...]) -> int:
    _ = arguments
    return 0


def test_startup_real_run_limits_preprocessing_to_owlv2_sam2(
    tmp_path: Path,
) -> None:
    # Given: startup will invoke preprocessing in real-run mode.
    image_root = tmp_path / "inputs"
    _ = _write_image(image_root / "source.jpg")
    calls: list[tuple[str, ...]] = []

    def preprocessing(arguments: tuple[str, ...]) -> int:
        calls.append(arguments)
        return 0

    # When: startup runs without --dry-run.
    exit_code = startup.run(
        ("real-lane-project", str(image_root)),
        workspace_root=tmp_path,
        stage_runners=StartupStageRunners(
            preprocessing=preprocessing,
            rough_masking=_successful_project_runner,
            visual_cue_generation=_successful_project_runner,
            rag=_successful_project_runner,
            prompt_generating=_successful_project_runner,
            mask_refining=_successful_cli,
            anomaly_grouping=_successful_project_runner,
            report_generating=_successful_project_runner,
        ),
    )

    # Then: preprocessing receives the only real lane implemented today.
    assert exit_code == 0
    assert len(calls) == 1
    assert "--dry-run" not in calls[0]
    assert calls[0][calls[0].index("--detector-lane") + 1] == "owlv2_sam2"
