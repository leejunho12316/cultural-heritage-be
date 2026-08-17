from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from modules.rag.operations.candidate_sidecars import (
    RAG_CANDIDATE_EVIDENCE_SIDECAR,
    CandidateRagSidecarResult,
    write_candidate_rag_sidecars,
)
from modules.shared import PathSafetyError

if TYPE_CHECKING:
    from pathlib import Path


def test_writer_rejects_temp_leaf_symlink_before_writing(tmp_path: Path) -> None:
    # Given: a sidecar temp leaf points at an external sentinel file.
    sidecar_root = tmp_path / "sidecars"
    sidecar_root.mkdir()
    external_file = tmp_path.parent / f"{tmp_path.name}-external-sentinel.txt"
    _ = external_file.write_text("sentinel", encoding="utf-8")
    temp_leaf = sidecar_root / f"{RAG_CANDIDATE_EVIDENCE_SIDECAR}.tmp"
    temp_leaf.symlink_to(external_file)

    # When: candidate sidecars are written.
    # Then: the writer fails closed before following the temp symlink.
    with pytest.raises(PathSafetyError):
        write_candidate_rag_sidecars(
            sidecar_root,
            CandidateRagSidecarResult(evidence_rows=(), cards=()),
        )
    assert external_file.read_text(encoding="utf-8") == "sentinel"
    assert not (sidecar_root / RAG_CANDIDATE_EVIDENCE_SIDECAR).exists()


def test_writer_rejects_final_leaf_symlink_before_writing(tmp_path: Path) -> None:
    # Given: a sidecar final leaf points at an external sentinel file.
    sidecar_root = tmp_path / "sidecars"
    sidecar_root.mkdir()
    external_file = tmp_path.parent / f"{tmp_path.name}-external-final.txt"
    _ = external_file.write_text("sentinel", encoding="utf-8")
    final_leaf = sidecar_root / RAG_CANDIDATE_EVIDENCE_SIDECAR
    final_leaf.symlink_to(external_file)

    # When: candidate sidecars are written.
    # Then: the writer fails closed before replacing the final symlink leaf.
    with pytest.raises(PathSafetyError):
        write_candidate_rag_sidecars(
            sidecar_root,
            CandidateRagSidecarResult(evidence_rows=(), cards=()),
        )
    assert external_file.read_text(encoding="utf-8") == "sentinel"
