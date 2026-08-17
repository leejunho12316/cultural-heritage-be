from __future__ import annotations

from pathlib import Path

STAGE_NAMES = (
    "preprocessing",
    "rough_masking",
    "visual_cue_generation",
    "rag",
    "prompt_generating",
    "mask_refining",
    "anomaly_grouping",
    "report_generating",
)

HELP_COMMANDS = (
    "uv run python -m modules.orchestration.startup --help",
    "uv run python -m modules.preprocessing.pipeline --help",
    "uv run python -m modules.mask_refining.refinement_cli --help",
    "uv run python -m modules.anomaly_grouping.runner --help",
    "uv run python -m modules.report_generating.runner --help",
    "uv run python -m modules.report_generating.browser_qa --help",
)


def test_pipeline_readme_documents_stage_order_and_help_commands() -> None:
    # Given: the repository-level pipeline README.
    readme_path = Path(__file__).resolve().parents[1] / "README.md"

    # When: the README content is inspected.
    content = readme_path.read_text(encoding="utf-8")

    # Then: it documents every pipeline stage and standalone CLI help command.
    for stage_name in STAGE_NAMES:
        assert stage_name in content
    for command in HELP_COMMANDS:
        assert command in content
    assert "startup_runner.py" in content
    assert "orchestration adapter" in content
