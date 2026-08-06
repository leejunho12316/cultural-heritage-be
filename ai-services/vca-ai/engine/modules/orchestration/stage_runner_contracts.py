"""Startup stage runner contracts and production defaults."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from modules.orchestration.stage_paths import StagePathMap

from modules.anomaly_grouping.startup_runner import run_anomaly_grouping_stage
from modules.mask_refining import refinement_cli
from modules.prompt_generating.startup_runner import run_prompt_generating_stage
from modules.rag.startup_runner import run_rag_stage
from modules.report_generating.startup_runner import run_report_generating_stage

type CliStageRunner = Callable[[tuple[str, ...]], int]


@dataclass(frozen=True, slots=True)
class ProjectStageRequest:
    """Project context passed to non-CLI stage runners."""

    project_name: str
    stage_name: str
    paths: StagePathMap
    device: str
    model_cache_root: Path
    dry_run: bool
    verify_model_hashes: bool


class ProjectStageRunner(Protocol):
    """Callable contract for project-scoped stage runners."""

    def __call__(self, request: ProjectStageRequest) -> int:
        """Run one project-scoped stage and return its exit code."""
        ...


def _run_lazy_preprocessing(arguments: tuple[str, ...]) -> int:
    from modules.preprocessing import (  # noqa: PLC0415
        pipeline as preprocessing_pipeline,
    )

    return preprocessing_pipeline.run(arguments)


def _run_lazy_rough_masking_stage(request: ProjectStageRequest) -> int:
    from modules.rough_masking.startup_runner import (  # noqa: PLC0415
        run_rough_masking_stage as run_stage,
    )

    return run_stage(request)


def _run_lazy_visual_cue_generation_stage(request: ProjectStageRequest) -> int:
    from modules.visual_cue_generation.startup_runner import (  # noqa: PLC0415
        run_visual_cue_generation_stage as run_stage,
    )

    return run_stage(request)


@dataclass(frozen=True, slots=True)
class StartupStageRunners:
    """All in-process runners used by startup orchestration."""

    preprocessing: CliStageRunner = _run_lazy_preprocessing
    rough_masking: ProjectStageRunner = _run_lazy_rough_masking_stage
    visual_cue_generation: ProjectStageRunner = _run_lazy_visual_cue_generation_stage
    rag: ProjectStageRunner = run_rag_stage
    prompt_generating: ProjectStageRunner = run_prompt_generating_stage
    mask_refining: CliStageRunner = refinement_cli.main
    anomaly_grouping: ProjectStageRunner = run_anomaly_grouping_stage
    report_generating: ProjectStageRunner = run_report_generating_stage
