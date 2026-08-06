"""RAG-owned non-approval sidecar payloads and validators."""

from collections.abc import Sequence
from typing import Final, NoReturn

from modules.rag.operations.budgeting import RagBudgetInputs, RagReopenInputs
from modules.shared import BudgetCounts, ContractValidationError, RagAccountingRow

type JsonScalar = str | int | bool
type JsonValue = JsonScalar | Sequence["JsonValue"] | dict[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]

RAG_BUDGET_INPUTS_SIDECAR: Final = "rag_budget_inputs.json"
RAG_REOPEN_INPUTS_SIDECAR: Final = "rag_reopen_inputs.json"
RAG_SIDECAR_FILENAMES: Final = frozenset(
    {RAG_BUDGET_INPUTS_SIDECAR, RAG_REOPEN_INPUTS_SIDECAR}
)


def budget_sidecar_payload(inputs: RagBudgetInputs) -> JsonObject:
    """Serialize RAG budget inputs without approval artifact fields."""
    return {
        "schema": "rag_budget_inputs_v1",
        "planned_counts": _counts_payload(inputs.planned_counts),
        "exceeded_thresholds": [
            threshold.value for threshold in inputs.exceeded_thresholds
        ],
        "requires_budget_review": inputs.requires_budget_review,
        "terminal_accounting_rows": _rows_payload(inputs.terminal_accounting_rows),
    }


def reopen_sidecar_payload(inputs: RagReopenInputs) -> JsonObject:
    """Serialize RAG reopen inputs without approval artifact fields."""
    return {
        "schema": "rag_reopen_inputs_v1",
        "reopened_candidate_ids": list(inputs.reopened_candidate_ids),
        "initial_planned_counts": _counts_payload(inputs.initial_planned_counts),
        "reopen_incremental_counts": _counts_payload(inputs.reopen_incremental_counts),
        "combined_planned_counts": _counts_payload(inputs.combined_planned_counts),
        "exceeded_thresholds": [
            threshold.value for threshold in inputs.exceeded_thresholds
        ],
        "requires_reopen_budget_review": inputs.requires_reopen_budget_review,
        "reasons": list(inputs.reasons),
        "terminal_accounting_rows": _rows_payload(inputs.terminal_accounting_rows),
    }


def validate_rag_budget_sidecar_payload(payload: JsonObject) -> None:
    """Validate a RAG budget sidecar payload shape."""
    _expect_schema(payload, "rag_budget_inputs_v1")
    _require_keys(
        payload,
        {
            "schema",
            "planned_counts",
            "exceeded_thresholds",
            "requires_budget_review",
            "terminal_accounting_rows",
        },
    )
    _validate_count_payload(payload["planned_counts"])
    _validate_rows_payload(payload["terminal_accounting_rows"])


def validate_rag_reopen_sidecar_payload(payload: JsonObject) -> None:
    """Validate a RAG reopen sidecar payload shape."""
    _expect_schema(payload, "rag_reopen_inputs_v1")
    _require_keys(
        payload,
        {
            "schema",
            "reopened_candidate_ids",
            "initial_planned_counts",
            "reopen_incremental_counts",
            "combined_planned_counts",
            "exceeded_thresholds",
            "requires_reopen_budget_review",
            "reasons",
            "terminal_accounting_rows",
        },
    )
    _validate_count_payload(payload["initial_planned_counts"])
    _validate_count_payload(payload["reopen_incremental_counts"])
    _validate_count_payload(payload["combined_planned_counts"])
    _validate_rows_payload(payload["terminal_accounting_rows"])


def _counts_payload(counts: BudgetCounts) -> JsonObject:
    return {
        "model_invocations": counts.model_invocations,
        "sam2_calls": counts.sam2_calls,
        "tiles": counts.tiles,
        "prompt_variants": counts.prompt_variants,
        "estimated_output_bytes": counts.estimated_output_bytes,
        "smoke_images": counts.smoke_images,
    }


def _rows_payload(rows: tuple[RagAccountingRow, ...]) -> list[JsonObject]:
    return [_row_payload(row) for row in rows]


def _row_payload(row: RagAccountingRow) -> JsonObject:
    return {
        "candidate_id": row.candidate_id,
        "followup_mode": row.followup_mode.value,
        "status": row.status.value,
        "qwen_status": row.qwen_status.value,
        "rag_query_terms": list(row.rag_query_terms),
        "rag_query_descriptors": list(row.rag_query_descriptors),
        "failure_reason": row.failure_reason or "",
        "terminal": row.terminal,
    }


def _expect_schema(payload: JsonObject, schema: str) -> None:
    if payload.get("schema") != schema:
        _raise_contract("schema", "unsupported RAG sidecar schema")


def _require_keys(payload: JsonObject, keys: set[str]) -> None:
    if set(payload) != keys:
        _raise_contract("payload", "must contain the expected fields")


def _validate_count_payload(value: JsonValue) -> None:
    if not isinstance(value, dict):
        _raise_contract("counts", "must be an object")
    required = {
        "model_invocations",
        "sam2_calls",
        "tiles",
        "prompt_variants",
        "estimated_output_bytes",
        "smoke_images",
    }
    if set(value) != required or any(
        not isinstance(count, int) or count < 0 for count in value.values()
    ):
        _raise_contract("counts", "must contain non-negative integer counts")


def _validate_rows_payload(value: JsonValue) -> None:
    if not isinstance(value, list):
        _raise_contract("terminal_accounting_rows", "must be a list")
    for row in value:
        if not isinstance(row, dict) or row.get("terminal") is not True:
            _raise_contract("terminal_accounting_rows", "must contain terminal rows")


def _raise_contract(field: str, reason: str) -> NoReturn:
    raise ContractValidationError(field, reason)
