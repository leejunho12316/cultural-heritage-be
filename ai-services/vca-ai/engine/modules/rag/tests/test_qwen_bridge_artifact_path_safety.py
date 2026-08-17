from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from modules.rag.qwen.qwen_bridge_artifacts import (
    QwenBridgeCandidateArtifact,
    qwen_bridge_results_path,
    write_qwen_bridge_results,
)
from modules.shared import (
    CandidateId,
    PathSafetyError,
    QwenBridgeResult,
    QwenBridgeStatus,
)

if TYPE_CHECKING:
    from pathlib import Path


def _successful_bridge() -> QwenBridgeResult:
    return QwenBridgeResult(
        candidate_id=CandidateId("candidate-001"),
        status=QwenBridgeStatus.SUCCESS,
        selected_terms=("white powder",),
        extracted_descriptors=("powdery",),
        confidence=0.91,
        reason="visual observation",
        qwen_observation_id="qwen-observation-001",
        input_view_hashes=("a" * 64,),
    )


def _artifact() -> QwenBridgeCandidateArtifact:
    return QwenBridgeCandidateArtifact(
        "lane-a/image-001-object-02/owlv2_sam2/records.json",
        0,
        _successful_bridge(),
    )


def test_qwen_bridge_writer_rejects_final_artifact_symlink(
    tmp_path: Path,
) -> None:
    # Given: the final artifact directory leaf is a symlink to an external directory.
    run_directory = tmp_path / "output" / "rag" / "run-001"
    run_directory.mkdir(parents=True)
    external_directory = tmp_path / "external-final-artifact"
    external_directory.mkdir()
    artifact_path = qwen_bridge_results_path(run_directory)
    artifact_path.symlink_to(external_directory, target_is_directory=True)

    # When: Qwen bridge artifacts are written.
    # Then: the writer fails closed with the shared path-safety error.
    with pytest.raises(PathSafetyError):
        _ = write_qwen_bridge_results(run_directory, (_artifact(),))


def test_qwen_bridge_writer_rejects_temp_artifact_symlink(
    tmp_path: Path,
) -> None:
    # Given: the temp artifact directory leaf is a symlink to an external directory.
    run_directory = tmp_path / "output" / "rag" / "run-001"
    run_directory.mkdir(parents=True)
    external_directory = tmp_path / "external-temp-artifact"
    external_directory.mkdir()
    temporary_artifact_path = run_directory / ".qwen_bridge_results.tmp"
    temporary_artifact_path.symlink_to(external_directory, target_is_directory=True)

    # When: Qwen bridge artifacts are written.
    # Then: the writer fails closed before deleting or replacing through the symlink.
    with pytest.raises(PathSafetyError):
        _ = write_qwen_bridge_results(run_directory, (_artifact(),))
