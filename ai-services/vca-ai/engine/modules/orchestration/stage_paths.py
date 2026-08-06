"""Project-scoped artifact roots for startup orchestration stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from pathlib import Path

from modules.shared import ContractValidationError, ensure_safe_run_root


@dataclass(frozen=True, slots=True)
class StagePathMap:
    """Project-scoped artifact roots for every startup stage."""

    preprocessing: Path
    rough_masking: Path
    visual_cue_generation: Path
    rag: Path
    prompt_generating: Path
    mask_refining: Path
    anomaly_grouping: Path
    report_generating: Path

    def output_dir(self, stage_name: str) -> Path:
        """Return the artifact root for a known stage name."""
        paths = {
            "preprocessing": self.preprocessing,
            "rough_masking": self.rough_masking,
            "visual_cue_generation": self.visual_cue_generation,
            "rag": self.rag,
            "prompt_generating": self.prompt_generating,
            "mask_refining": self.mask_refining,
            "anomaly_grouping": self.anomaly_grouping,
            "report_generating": self.report_generating,
        }
        path = paths.get(stage_name)
        if path is None:
            _invalid_stage_name(stage_name)
        return path


def stage_paths(workspace_root: Path, project_name: str) -> StagePathMap:
    """Build containment-checked module-owned output roots for a project."""
    return StagePathMap(
        _safe_stage_path(workspace_root, "preprocessing", project_name),
        _safe_stage_path(workspace_root, "rough_masking", project_name),
        _safe_stage_path(workspace_root, "visual_cue_generation", project_name),
        _safe_stage_path(workspace_root, "rag", project_name),
        _safe_stage_path(workspace_root, "prompt_generating", project_name),
        _safe_stage_path(workspace_root, "mask_refining", project_name),
        _safe_stage_path(workspace_root, "anomaly_grouping", project_name),
        _safe_stage_path(workspace_root, "report_generating", project_name),
    )


def _safe_stage_path(workspace_root: Path, module_name: str, project_name: str) -> Path:
    return ensure_safe_run_root(
        workspace_root, workspace_root / "output" / module_name / project_name
    )


def _invalid_stage_name(stage_name: str) -> NoReturn:
    field = "stage_name"
    raise ContractValidationError(field, stage_name)
