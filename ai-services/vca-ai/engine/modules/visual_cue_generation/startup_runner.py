"""Project-level startup runner for Qwen visual cue generation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from modules.mask_refining import PillowQwenViewRenderer, load_transformers_qwen_backend
from modules.rough_masking.startup_manifest import load_startup_manifest
from modules.shared import (
    QWEN_MODEL_ID,
    ContractValidationError,
    ExitCode,
    resolve_model_path,
)
from modules.visual_cue_generation.generation import generate_qwen_bridge_results
from modules.visual_cue_generation.models import QwenBridgeGenerationInputs

if TYPE_CHECKING:
    from modules.orchestration.stage_paths import StagePathMap


class _VisualCueStageRequest(Protocol):
    """Project context needed by the visual-cue startup runner."""

    @property
    def paths(self) -> StagePathMap: ...

    @property
    def device(self) -> str: ...

    @property
    def model_cache_root(self) -> Path: ...

    @property
    def dry_run(self) -> bool: ...


def run_visual_cue_generation_stage(request: _VisualCueStageRequest) -> int:
    """Run Qwen bridge generation for one startup project."""
    try:
        _require_manifest_status(request)
        if request.dry_run or _skip_visual_cues():
            return int(ExitCode.OK)
        inputs = _generation_inputs(request)
        renderer = PillowQwenViewRenderer(inputs.asset_root)
        backend = load_transformers_qwen_backend(
            qwen_model_directory(request.model_cache_root),
            request.device,
        )
        _ = generate_qwen_bridge_results(inputs, renderer, backend)
    except (ContractValidationError, OSError):
        return int(ExitCode.INCOMPLETE_OR_FAILURE)
    return int(ExitCode.OK)


def _skip_visual_cues() -> bool:
    return os.environ.get("VCA_SKIP_VISUAL_CUES", "false") == "true"


def _require_manifest_status(request: _VisualCueStageRequest) -> None:
    manifest = load_startup_manifest(request.paths.preprocessing)
    if request.dry_run:
        if manifest.detector_lane_status != "dry_run_not_executed":
            field = "preprocessing_manifest"
            reason = "dry-run required"
            raise ContractValidationError(field, reason)
        return
    if manifest.detector_lane_status != "real_executed":
        field = "preprocessing_manifest"
        reason = "real run required"
        raise ContractValidationError(field, reason)


def _generation_inputs(request: _VisualCueStageRequest) -> QwenBridgeGenerationInputs:
    shared_asset_root = request.paths.preprocessing.parent.parent
    return QwenBridgeGenerationInputs(
        rough_root=request.paths.rough_masking,
        rag_run_dir=request.paths.rag,
        asset_root=shared_asset_root,
        input_manifest_path=(
            request.paths.preprocessing / "manifests" / "input_manifest.json"
        ),
        device=request.device,
    )


def qwen_model_directory(model_cache_root: Path) -> Path:
    """Resolve Qwen's configured local model directory."""
    return resolve_model_path(
        "qwen2.5-vl.visual",
        Path("models") / "hf" / QWEN_MODEL_ID,
        model_cache_root,
    )
