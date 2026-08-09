from __future__ import annotations

import importlib
import importlib.util
from typing import TYPE_CHECKING

from modules import rag
from modules.mask_refining import QwenEvidenceResult
from modules.prompt_generating import (
    ConceptCard,
    PromptVariant,
    VisualConceptFamily,
    render_lane_specific_variants,
    validate_executable_prompt,
    validate_unique_prompt_texts,
)
from modules.shared import (
    BUDGET_THRESHOLDS,
    BudgetCounts,
    BudgetThreshold,
    BudgetThresholds,
    ExportCitationBridge,
    FollowupMode,
    HybridDescriptor,
    QwenBridgeResult,
    QwenRagQueryInput,
    RagAccountingRow,
    RagAccountingStatus,
    RelationAuthorityInput,
    ReopenRequestHashInput,
    UserFollowupRequest,
    ensure_source_document_is_not_write_target,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_rag_package_reexports_shared_contracts_without_local_variants() -> None:
    # Given: RAG contract symbols that must stay shared-owned.
    shared_contracts = {
        "BUDGET_THRESHOLDS": BUDGET_THRESHOLDS,
        "BudgetCounts": BudgetCounts,
        "BudgetThreshold": BudgetThreshold,
        "BudgetThresholds": BudgetThresholds,
        "ExportCitationBridge": ExportCitationBridge,
        "FollowupMode": FollowupMode,
        "HybridDescriptor": HybridDescriptor,
        "QwenBridgeResult": QwenBridgeResult,
        "QwenRagQueryInput": QwenRagQueryInput,
        "RagAccountingRow": RagAccountingRow,
        "RagAccountingStatus": RagAccountingStatus,
        "RelationAuthorityInput": RelationAuthorityInput,
        "ReopenRequestHashInput": ReopenRequestHashInput,
        "UserFollowupRequest": UserFollowupRequest,
        "ensure_source_document_is_not_write_target": (
            ensure_source_document_is_not_write_target
        ),
    }

    # When: the RAG package boundary is inspected.
    exported_contracts = {name: getattr(rag, name) for name in shared_contracts}

    # Then: every shared-owned contract is imported by identity, not locally defined.
    assert exported_contracts == shared_contracts
    assert importlib.util.find_spec("modules.rag.statuses") is None


def test_rag_package_reexports_prompt_generation_contracts() -> None:
    # Given: prompt generation remains owned by modules.prompt_generating.
    prompt_contracts = {
        "ConceptCard": ConceptCard,
        "PromptVariant": PromptVariant,
        "VisualConceptFamily": VisualConceptFamily,
        "render_lane_specific_variants": render_lane_specific_variants,
        "validate_executable_prompt": validate_executable_prompt,
        "validate_unique_prompt_texts": validate_unique_prompt_texts,
    }

    # When: RAG exposes the prompt seam needed by later adapters.
    exported_contracts = {name: getattr(rag, name) for name in prompt_contracts}

    # Then: prompt safety/rendering is consumed by identity, not reimplemented.
    assert exported_contracts == prompt_contracts
    assert importlib.util.find_spec("modules.rag.prompt_safety") is None


def test_rag_import_has_no_working_directory_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: imports run from an empty directory that would reveal accidental writes.
    monkeypatch.chdir(tmp_path)
    before_entries = tuple(tmp_path.iterdir())

    # When: the package is imported through the public import system.
    imported = importlib.import_module("modules.rag")

    # Then: import is quiet and does not materialize files or directories.
    captured = capsys.readouterr()
    assert imported is rag
    assert captured.out == ""
    assert captured.err == ""
    assert tuple(tmp_path.iterdir()) == before_entries


def test_qwen_conversion_stays_outside_rag_package() -> None:
    # Given: mask_refining owns local Qwen evidence to shared bridge conversion.
    converter = QwenEvidenceResult.to_bridge_result

    # When: RAG's package surface is inspected.
    has_local_converter = hasattr(rag, "QwenEvidenceResult")

    # Then: RAG consumes shared bridge records and does not expose local Qwen records.
    assert converter.__module__ == "modules.mask_refining.contracts.models"
    assert rag.QwenBridgeResult is QwenBridgeResult
    assert has_local_converter is False
