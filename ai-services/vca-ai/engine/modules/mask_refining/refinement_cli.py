"""Command-line entry point for prompt-driven local mask refinement."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from modules.mask_refining.execution import run_refinement
from modules.mask_refining.execution.assets import PreprocessingAssetInputError
from modules.mask_refining.execution.models import RefinementRunRequest
from modules.mask_refining.execution.prompts import PromptArtifactInputError
from modules.shared import ContractValidationError

if TYPE_CHECKING:
    from collections.abc import Sequence


class _CliNamespace(argparse.Namespace):
    prompt_output_dir: Path
    rough_root: Path
    asset_root: Path
    output_dir: Path
    model_cache_root: Path
    device: str
    no_verify_model_hashes: bool
    max_groups: int | None
    progress_root: Path | None

    def __init__(self) -> None:
        """Initialize typed defaults before argparse mutates the namespace."""
        super().__init__()
        self.prompt_output_dir = Path()
        self.rough_root = Path()
        self.asset_root = Path()
        self.output_dir = Path()
        self.model_cache_root = Path("models")
        self.device = ""
        self.no_verify_model_hashes = False
        self.max_groups = None
        self.progress_root = None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "--prompt-output-dir",
        required=True,
        type=Path,
        help="prompt_generating output directory with refinement prompt variants",
    )
    _ = parser.add_argument(
        "--rough-root",
        required=True,
        type=Path,
        help="rough_masking output root with accepted parent candidates",
    )
    _ = parser.add_argument(
        "--asset-root",
        required=True,
        type=Path,
        help="preprocessing asset root with ROI crops and object masks",
    )
    _ = parser.add_argument("--output-dir", required=True, type=Path)
    _ = parser.add_argument("--model-cache-root", default=Path("models"), type=Path)
    _ = parser.add_argument("--device", required=True)
    _ = parser.add_argument("--no-verify-model-hashes", action="store_true")
    _ = parser.add_argument(
        "--max-groups",
        type=int,
        default=None,
        help="maximum prompt groups to consume; skipped groups count toward the limit",
    )
    _ = parser.add_argument(
        "--progress-root",
        type=Path,
        default=None,
        help="project output root to report mid-stage progress counts into",
    )
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    """Run refinement and reserve exit code two for invalid input contracts."""
    parsed = _CliNamespace()
    _ = _parser().parse_args(arguments, namespace=parsed)
    request = RefinementRunRequest(
        parsed.prompt_output_dir,
        parsed.rough_root,
        parsed.asset_root,
        parsed.output_dir,
        parsed.model_cache_root,
        parsed.device,
        not parsed.no_verify_model_hashes,
        parsed.max_groups,
        parsed.progress_root,
    )
    try:
        _ = run_refinement(request)
    except (
        PromptArtifactInputError,
        PreprocessingAssetInputError,
        ContractValidationError,
    ) as error:
        print(  # noqa: T201
            f"mask_refining: failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
