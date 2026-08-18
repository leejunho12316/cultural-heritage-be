from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from modules.mask_refining.execution import run_refinement
from modules.mask_refining.execution import runner as execution
from modules.mask_refining.execution.models import RefinementRunRequest
from modules.shared import PathSafetyError

if TYPE_CHECKING:
    from pathlib import Path

    from modules.visual_cue_generation.rough_records import RoughQwenCandidate


@pytest.mark.parametrize(
    "leaf_name", ["refined_records.jsonl", "skips.jsonl", "manifest.json"]
)
def test_refinement_rejects_fixed_artifact_symlink_leaf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    leaf_name: str,
) -> None:
    # Given: one fixed refinement artifact leaf points at an external file.
    prompt_root = tmp_path / "prompts"
    _write_empty_prompt_inputs(prompt_root)
    output_root = tmp_path / "output"
    output_root.mkdir()
    external = tmp_path / f"external-{leaf_name}"
    _ = external.write_text("sentinel", encoding="utf-8")
    (output_root / leaf_name).symlink_to(external)

    def rough_candidates(
        _rough_root: Path, _asset_root: Path
    ) -> tuple[RoughQwenCandidate, ...]:
        return ()

    monkeypatch.setattr(execution, "_rough_qwen_candidates", rough_candidates)

    # When / Then: refinement rejects the leaf before clobbering the target.
    with pytest.raises(PathSafetyError, match="refinement artifact leaf"):
        _ = run_refinement(
            RefinementRunRequest(
                prompt_output_dir=prompt_root,
                rough_root=tmp_path / "rough",
                asset_root=tmp_path / "assets",
                output_dir=output_root,
                model_cache_root=tmp_path / "models",
                device="cpu",
                verify_model_hashes=False,
                max_groups=0,
            )
        )
    assert external.read_text(encoding="utf-8") == "sentinel"


def _write_empty_prompt_inputs(root: Path) -> None:
    root.mkdir()
    _ = (root / "manifest.json").write_text(
        json.dumps({"schema": "rag_refinement_prompt_variants_smoke_v1"}),
        encoding="utf-8",
    )
    _ = (root / "rag_refinement_prompt_variants.jsonl").write_text(
        "", encoding="utf-8"
    )
