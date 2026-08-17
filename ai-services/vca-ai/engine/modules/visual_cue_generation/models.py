"""Public contracts for Qwen bridge generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class QwenBridgeGenerationInputs:
    """Paths and runtime options for rough-mask Qwen generation."""

    rough_root: Path
    rag_run_dir: Path
    asset_root: Path
    input_manifest_path: Path
    device: str


@dataclass(frozen=True, slots=True)
class QwenBridgeGenerationResult:
    """Summary of the run-local Qwen bridge result directory."""

    artifact_path: Path
    processed_candidates: int
    successful_results: int
    failed_results: int
