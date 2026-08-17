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
    output_root: Path


class ProjectStageRunner(Protocol):
    """Callable contract for project-scoped stage runners."""

    def __call__(self, request: ProjectStageRequest) -> int:
        """Run one project-scoped stage and return its exit code."""
        ...


# preprocessing CLI 러너를 지연 실행한다. StartupStageRunners.preprocessing의
# 기본값으로 쓰인다.
def _run_lazy_preprocessing(arguments: tuple[str, ...]) -> int:
    # 지연 임포트: 이 계약 모듈을 임포트할 때 PIL/torch/transformers/sam2가
    # 함께 로드되면 안 된다는 정책이며, test_stage_runner_contract_imports.py가
    # 이를 강제한다.
    from modules.preprocessing import (  # noqa: PLC0415
        pipeline as preprocessing_pipeline,
    )

    return preprocessing_pipeline.run(arguments)


# rough_masking 스테이지 러너를 지연 실행한다. 동일하게 무거운 비전 런타임을
# 계약 모듈 임포트 시점에 로드하지 않기 위함이다.
# StartupStageRunners.rough_masking의 기본값으로 쓰인다.
def _run_lazy_rough_masking_stage(request: ProjectStageRequest) -> int:
    from modules.rough_masking.startup_runner import (  # noqa: PLC0415
        run_rough_masking_stage as run_stage,
    )

    return run_stage(request)


# visual_cue_generation 스테이지 러너를 지연 실행한다. 위와 같은 이유로
# 무거운 런타임 임포트를 실제 실행 시점까지 미룬다.
# StartupStageRunners.visual_cue_generation의 기본값으로 쓰인다.
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
