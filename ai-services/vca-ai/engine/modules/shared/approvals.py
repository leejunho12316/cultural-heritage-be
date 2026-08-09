"""Pure budget-approval schemas and identity predicates for orchestration."""

from dataclasses import dataclass, field

from modules.shared.constants import (
    BUDGET_APPROVAL_REQUEST_SCHEMA_VERSION,
    BUDGET_APPROVAL_SCHEMA_VERSION,
)
from modules.shared.hash_inputs import PlannedCountHashInput
from modules.shared.hashes import planned_count_hash
from modules.shared.models import (
    BUDGET_THRESHOLDS,
    BudgetCounts,
    BudgetThreshold,
    DryRunId,
    FollowupHash,
    ImageId,
    PlannedCountHash,
)


@dataclass(frozen=True, slots=True)
class BudgetApprovalRequest:
    """Typed content for orchestration-owned budget_approval_request.json."""

    schema_version: str = field(
        init=False, default=BUDGET_APPROVAL_REQUEST_SCHEMA_VERSION
    )
    dry_run_id: DryRunId
    image_subset_ids: tuple[ImageId, ...]
    followup_request_hash: FollowupHash
    planned_counts: BudgetCounts
    exceeded_thresholds: tuple[BudgetThreshold, ...]
    requires_user_budget_approval: bool
    planned_count_hash: PlannedCountHash


@dataclass(frozen=True, slots=True)
class BudgetApproval:
    """Typed content for orchestration-owned budget_approval.json."""

    schema_version: str = field(init=False, default=BUDGET_APPROVAL_SCHEMA_VERSION)
    approved_by: str
    approved_at: str
    dry_run_id: DryRunId
    image_subset_ids: tuple[ImageId, ...]
    approved_max_counts: BudgetCounts
    followup_request_hash: FollowupHash
    planned_count_hash: PlannedCountHash
    accepted_thresholds: tuple[BudgetThreshold, ...]


def budget_thresholds_exceeded(counts: BudgetCounts) -> tuple[BudgetThreshold, ...]:
    """Return approval-gate dimensions exceeded without truncating counts."""
    exceeded: list[BudgetThreshold] = []
    if (
        counts.model_invocations
        > BUDGET_THRESHOLDS.max_planned_model_invocations_without_approval
    ):
        exceeded.append(BudgetThreshold.MODEL_INVOCATIONS)
    if counts.sam2_calls > BUDGET_THRESHOLDS.max_planned_sam2_calls_without_approval:
        exceeded.append(BudgetThreshold.SAM2_CALLS)
    if counts.tiles > BUDGET_THRESHOLDS.max_planned_tiles_without_approval:
        exceeded.append(BudgetThreshold.TILES)
    if (
        counts.prompt_variants
        > BUDGET_THRESHOLDS.max_planned_prompt_variants_without_approval
    ):
        exceeded.append(BudgetThreshold.PROMPT_VARIANTS)
    output_limit_bytes = (
        BUDGET_THRESHOLDS.max_estimated_output_gb_without_approval * 1024**3
    )
    if counts.estimated_output_bytes > output_limit_bytes:
        exceeded.append(BudgetThreshold.ESTIMATED_OUTPUT_GB)
    if counts.smoke_images > BUDGET_THRESHOLDS.max_smoke_images_without_approval:
        exceeded.append(BudgetThreshold.SMOKE_IMAGES)
    return tuple(exceeded)


def make_budget_approval_request(value: PlannedCountHashInput) -> BudgetApprovalRequest:
    """Create a pure approval-request value without reading or writing artifacts."""
    return BudgetApprovalRequest(
        dry_run_id=value.dry_run_id,
        image_subset_ids=value.image_subset_ids,
        followup_request_hash=value.followup_request_hash,
        planned_counts=value.counts,
        exceeded_thresholds=value.exceeded_thresholds,
        requires_user_budget_approval=bool(value.exceeded_thresholds),
        planned_count_hash=planned_count_hash(value),
    )


def user_budget_approval(
    request: BudgetApprovalRequest, *, approved_at: str
) -> BudgetApproval:
    """Construct a typed user approval value without validating an artifact file."""
    return BudgetApproval(
        approved_by="user",
        approved_at=approved_at,
        dry_run_id=request.dry_run_id,
        image_subset_ids=request.image_subset_ids,
        approved_max_counts=request.planned_counts,
        followup_request_hash=request.followup_request_hash,
        planned_count_hash=request.planned_count_hash,
        accepted_thresholds=request.exceeded_thresholds,
    )


def budget_approval_matches_request(
    approval: BudgetApproval, request: BudgetApprovalRequest
) -> bool:
    """Compare identity fields for stale-approval detection without file validation."""
    return (
        approval.approved_by == "user"
        and approval.dry_run_id == request.dry_run_id
        and approval.followup_request_hash == request.followup_request_hash
        and approval.planned_count_hash == request.planned_count_hash
    )
