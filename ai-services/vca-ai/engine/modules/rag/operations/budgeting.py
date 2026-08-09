"""RAG-owned budget input components for orchestration."""

from dataclasses import dataclass

from modules.shared import (
    BudgetCounts,
    BudgetThreshold,
    ContractValidationError,
    RagAccountingRow,
    budget_thresholds_exceeded,
)


@dataclass(frozen=True, slots=True)
class RagBudgetInputs:
    """RAG budget components before orchestration approval handling."""

    planned_counts: BudgetCounts
    exceeded_thresholds: tuple[BudgetThreshold, ...]
    requires_budget_review: bool
    terminal_accounting_rows: tuple[RagAccountingRow, ...]


# 계획된 카운트가 예산 임계값을 넘는지 계산해 승인 검토가 필요한지 판정한다.
# orchestration이 RAG 후속 작업 예산 승인 흐름 진입 전에 호출한다.
def build_rag_budget_inputs(
    planned_counts: BudgetCounts,
    accounting_rows: tuple[RagAccountingRow, ...],
) -> RagBudgetInputs:
    """Return RAG budget inputs without approval artifact decisions."""
    _require_terminal_rows(accounting_rows)
    exceeded = budget_thresholds_exceeded(planned_counts)
    return RagBudgetInputs(
        planned_counts=planned_counts,
        exceeded_thresholds=exceeded,
        requires_budget_review=bool(exceeded),
        terminal_accounting_rows=accounting_rows,
    )


def _require_terminal_rows(rows: tuple[RagAccountingRow, ...]) -> None:
    if any(not row.terminal for row in rows):
        field = "accounting_rows"
        reason = "must all be terminal"
        raise ContractValidationError(field, reason)
