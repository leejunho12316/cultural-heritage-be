"""RAG-owned budget and reopen input components for orchestration."""

from dataclasses import dataclass

from modules.shared import (
    BudgetCounts,
    BudgetThreshold,
    CandidateId,
    ContractValidationError,
    DryRunId,
    FollowupHash,
    PlannedCountHash,
    RagAccountingRow,
    ReopenRequestHashInput,
    RuntimeMetadata,
    budget_thresholds_exceeded,
)


@dataclass(frozen=True, slots=True)
class RagBudgetInputs:
    """RAG budget components before orchestration approval handling."""

    planned_counts: BudgetCounts
    exceeded_thresholds: tuple[BudgetThreshold, ...]
    requires_budget_review: bool
    terminal_accounting_rows: tuple[RagAccountingRow, ...]


@dataclass(frozen=True, slots=True)
class RagReopenInputs:
    """RAG reopened-work components before orchestration approval handling."""

    reopened_candidate_ids: tuple[CandidateId, ...]
    initial_planned_counts: BudgetCounts
    reopen_incremental_counts: BudgetCounts
    combined_planned_counts: BudgetCounts
    exceeded_thresholds: tuple[BudgetThreshold, ...]
    requires_reopen_budget_review: bool
    reasons: tuple[str, ...]
    terminal_accounting_rows: tuple[RagAccountingRow, ...]


@dataclass(frozen=True, slots=True)
class ReopenHashContext:
    """Orchestration identifiers needed for a shared reopen hash input."""

    dry_run_id: DryRunId
    followup_request_hash: FollowupHash
    planned_count_hash: PlannedCountHash
    threshold_cap_config_version: str
    runtime_metadata: RuntimeMetadata


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


# 재오픈으로 추가된 카운트를 기존 계획과 합산해 예산 임계값 재초과 여부를
# 판정한다. build_rag_budget_inputs의 재오픈 버전이며 orchestration이 호출한다.
def build_rag_reopen_inputs(
    reopened_candidate_ids: tuple[CandidateId, ...],
    initial_planned_counts: BudgetCounts,
    reopen_incremental_counts: BudgetCounts,
    reasons: tuple[str, ...],
    accounting_rows: tuple[RagAccountingRow, ...],
) -> RagReopenInputs:
    """Return reopened-work inputs without approval artifact decisions."""
    _require_terminal_rows(accounting_rows)
    combined = _add_counts(initial_planned_counts, reopen_incremental_counts)
    exceeded = budget_thresholds_exceeded(combined)
    return RagReopenInputs(
        reopened_candidate_ids=reopened_candidate_ids,
        initial_planned_counts=initial_planned_counts,
        reopen_incremental_counts=reopen_incremental_counts,
        combined_planned_counts=combined,
        exceeded_thresholds=exceeded,
        requires_reopen_budget_review=bool(exceeded),
        reasons=reasons,
        terminal_accounting_rows=accounting_rows,
    )


# 재오픈 요청의 재현성/감사를 위해 공유 해시 입력을 조립한다.
# build_rag_reopen_inputs 이후 orchestration의 승인 아티팩트 생성 단계에서
# 호출한다.
def build_reopen_request_hash_input(
    context: ReopenHashContext,
    reopen_inputs: RagReopenInputs,
) -> ReopenRequestHashInput:
    """Build the shared hash input from RAG-owned reopen components."""
    return ReopenRequestHashInput(
        dry_run_id=context.dry_run_id,
        followup_request_hash=context.followup_request_hash,
        planned_count_hash=context.planned_count_hash,
        reopened_candidate_ids=reopen_inputs.reopened_candidate_ids,
        initial_planned_counts=reopen_inputs.initial_planned_counts,
        reopen_incremental_counts=reopen_inputs.reopen_incremental_counts,
        combined_planned_counts=reopen_inputs.combined_planned_counts,
        exceeded_thresholds=reopen_inputs.exceeded_thresholds,
        threshold_cap_config_version=context.threshold_cap_config_version,
        runtime_metadata=context.runtime_metadata,
    )


def _require_terminal_rows(rows: tuple[RagAccountingRow, ...]) -> None:
    if any(not row.terminal for row in rows):
        field = "accounting_rows"
        reason = "must all be terminal"
        raise ContractValidationError(field, reason)


def _add_counts(first: BudgetCounts, second: BudgetCounts) -> BudgetCounts:
    return BudgetCounts(
        model_invocations=first.model_invocations + second.model_invocations,
        sam2_calls=first.sam2_calls + second.sam2_calls,
        tiles=first.tiles + second.tiles,
        prompt_variants=first.prompt_variants + second.prompt_variants,
        estimated_output_bytes=(
            first.estimated_output_bytes + second.estimated_output_bytes
        ),
        smoke_images=first.smoke_images + second.smoke_images,
    )
