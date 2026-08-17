from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules.rag.operations.budgeting import build_rag_budget_inputs
from modules.rag.operations.io import write_rag_sidecar_atomic
from modules.rag.operations.sidecars import (
    RAG_BUDGET_INPUTS_SIDECAR,
    JsonObject,
    budget_sidecar_payload,
    validate_rag_budget_sidecar_payload,
)
from modules.shared import (
    BudgetCounts,
    CandidateId,
    ContractValidationError,
    FollowupMode,
    PathSafetyError,
    QwenBridgeStatus,
    RagAccountingRow,
    RagAccountingStatus,
)


def _terminal_row() -> RagAccountingRow:
    return RagAccountingRow(
        candidate_id=CandidateId("candidate-001"),
        followup_mode=FollowupMode.AUTOMATIC,
        status=RagAccountingStatus.COMPLETED,
        qwen_status=QwenBridgeStatus.SUCCESS,
        rag_query_terms=("white deposit",),
        rag_query_descriptors=("powdery",),
    )


def _budget_payload() -> JsonObject:
    return budget_sidecar_payload(
        build_rag_budget_inputs(
            planned_counts=BudgetCounts(1, 0, 0, 0, 0, 0),
            accounting_rows=(_terminal_row(),),
        )
    )


def test_rag_sidecar_payloads_are_deterministic_and_validate_shared_rows() -> None:
    # Given: RAG-owned budget input components.
    budget_inputs = build_rag_budget_inputs(
        planned_counts=BudgetCounts(1, 0, 0, 0, 0, 0),
        accounting_rows=(_terminal_row(),),
    )

    # When: the sidecar payload is serialized and validated.
    budget_payload = budget_sidecar_payload(budget_inputs)

    # Then: the payload uses the RAG-owned schema and contains terminal rows only.
    assert budget_payload["schema"] == "rag_budget_inputs_v1"
    validate_rag_budget_sidecar_payload(budget_payload)


def test_atomic_write_validates_temp_before_rename(tmp_path: Path) -> None:
    # Given: a RAG-owned sidecar target and valid payload.
    target = tmp_path / RAG_BUDGET_INPUTS_SIDECAR
    payload = _budget_payload()

    # When: RAG writes the sidecar atomically.
    write_rag_sidecar_atomic(target, payload, validate_rag_budget_sidecar_payload)

    # Then: only the final file remains and it contains deterministic JSON.
    assert target.exists()
    assert not target.with_suffix(".json.tmp").exists()
    assert json.loads(target.read_text(encoding="utf-8")) == payload


def test_atomic_writer_rejects_final_leaf_symlink_before_writing(
    tmp_path: Path,
) -> None:
    # Given: a final sidecar leaf points at an external sentinel file.
    external_file = tmp_path.parent / f"{tmp_path.name}-final-sentinel.json"
    _ = external_file.write_text("sentinel", encoding="utf-8")
    target = tmp_path / RAG_BUDGET_INPUTS_SIDECAR
    target.symlink_to(external_file)

    # When: RAG writes a sidecar atomically.
    # Then: it fails closed before replacing the final symlink leaf.
    with pytest.raises(PathSafetyError):
        write_rag_sidecar_atomic(
            target,
            _budget_payload(),
            validate_rag_budget_sidecar_payload,
        )
    assert external_file.read_text(encoding="utf-8") == "sentinel"


def test_atomic_writer_rejects_temp_leaf_symlink_before_writing(
    tmp_path: Path,
) -> None:
    # Given: a temp sidecar leaf points at an external sentinel file.
    external_file = tmp_path.parent / f"{tmp_path.name}-temp-sentinel.json"
    _ = external_file.write_text("sentinel", encoding="utf-8")
    target = tmp_path / RAG_BUDGET_INPUTS_SIDECAR
    temporary_target = target.with_suffix(f"{target.suffix}.tmp")
    temporary_target.symlink_to(external_file)

    # When: RAG writes a sidecar atomically.
    # Then: it fails closed before following the temp symlink leaf.
    with pytest.raises(PathSafetyError):
        write_rag_sidecar_atomic(
            target,
            _budget_payload(),
            validate_rag_budget_sidecar_payload,
        )
    assert external_file.read_text(encoding="utf-8") == "sentinel"
    assert not target.exists()


def test_atomic_write_failure_leaves_no_final_or_temp_sidecar(tmp_path: Path) -> None:
    # Given: an invalid payload for a RAG-owned sidecar target.
    target = tmp_path / RAG_BUDGET_INPUTS_SIDECAR

    # When/Then: validation fails before final rename and cleans up the temp file.
    with pytest.raises(ContractValidationError):
        write_rag_sidecar_atomic(
            target,
            {"schema": "wrong"},
            validate_rag_budget_sidecar_payload,
        )
    assert not target.exists()
    assert not target.with_suffix(".json.tmp").exists()


def test_atomic_writer_accepts_only_rag_owned_sidecars(tmp_path: Path) -> None:
    # Given: an approval-like filename that RAG must not own.
    target = tmp_path / "approval.json"

    # When/Then: RAG rejects non-allowlisted sidecar filenames.
    with pytest.raises(ContractValidationError, match="sidecar"):
        write_rag_sidecar_atomic(
            target,
            {"schema": "x"},
            validate_rag_budget_sidecar_payload,
        )


def test_atomic_writer_rejects_source_document_targets(tmp_path: Path) -> None:
    # Given: a source-document root and a target inside that root.
    source_root = tmp_path / "source-documents"
    source_root.mkdir()
    target = source_root / RAG_BUDGET_INPUTS_SIDECAR

    # When/Then: the optional source-document no-write guard remains active.
    with pytest.raises(PathSafetyError):
        write_rag_sidecar_atomic(
            target,
            _budget_payload(),
            validate_rag_budget_sidecar_payload,
            source_document_root=source_root,
        )


def test_rag_sidecar_and_io_sources_have_no_approval_artifact_ownership() -> None:
    # Given: RAG sidecar and I/O source code.
    source = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "modules/rag/operations/sidecars.py",
            "modules/rag/operations/io.py",
        )
    )

    # When/Then: approval artifact ownership strings are absent.
    forbidden = (
        "budget_approval_request.json",
        "budget_approval.json",
        "make_budget_approval_request",
        "user_budget_approval",
        "budget_approval_matches_request",
    )
    assert all(value not in source for value in forbidden)
