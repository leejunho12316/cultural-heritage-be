"""Immutable value objects shared by pipeline modules."""

from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import NewType

from modules.shared.constants import USER_FOLLOWUP_REQUEST_SCHEMA_VERSION
from modules.shared.errors import ContractValidationError
from modules.shared.lanes import RagLane

CandidateId = NewType("CandidateId", str)
ImageId = NewType("ImageId", str)
FollowupHash = NewType("FollowupHash", str)
DryRunId = NewType("DryRunId", str)
PlannedCountHash = NewType("PlannedCountHash", str)
SourceImageManifestHash = NewType("SourceImageManifestHash", str)


class PromptRole(StrEnum):
    """Prompt-pack roles retained as metadata, not executable seed text."""

    STATIC_SEED = "static_seed"
    STATIC_FALLBACK_ABLATION = "static_fallback_ablation"
    RAG_REFINEMENT = "rag_refinement"


class FollowupSelectorType(StrEnum):
    """Allowed user-request parent-target selectors."""

    OBJECT_ID = "object_id"
    TILE_VIEW_ID = "tile_view_id"
    CANDIDATE_ID = "candidate_id"
    SAME_ANOMALY_GROUP_ID = "same_anomaly_group_id"
    RELATION_GROUP_ID = "relation_group_id"


def parse_followup_selector_type(raw_selector_type: str) -> FollowupSelectorType:
    """Parse a user-provided selector type into its closed enum."""
    try:
        return FollowupSelectorType(raw_selector_type)
    except ValueError as error:
        field = "selector_type"
        raise ContractValidationError(field, raw_selector_type) from error


def parse_user_followup_schema_version(raw_schema_version: str) -> str:
    """Validate the only accepted user follow-up schema version."""
    if raw_schema_version != USER_FOLLOWUP_REQUEST_SCHEMA_VERSION:
        field = "schema_version"
        raise ContractValidationError(field, raw_schema_version)
    return raw_schema_version


class FollowupMode(StrEnum):
    """Origin labels for shared RAG target accounting."""

    AUTOMATIC = "automatic"
    USER_REQUESTED = "user_requested"


class LaneExecutionStatus(StrEnum):
    """Detector lane execution outcomes recorded on one adapter receipt."""

    REAL_EXECUTED = "real_executed"
    SKIPPED_NOT_REQUESTED = "skipped_not_requested"
    BLOCKED = "blocked"
    FAILED = "failed"


class RunStatus(StrEnum):
    """Top-level pipeline status values."""

    SUCCESS = "success"
    INCOMPLETE = "incomplete"
    INCOMPLETE_PRE_QWEN_PREVIEW = "incomplete_pre_qwen_preview"
    NO_VALID_ROUGH_TARGETS = "no_valid_rough_targets"
    BLOCKED = "blocked"
    FAILURE = "failure"


class ExitCode(IntEnum):
    """Pipeline process exit codes."""

    OK = 0
    INCOMPLETE_OR_FAILURE = 2


@dataclass(frozen=True, slots=True)
class PromptMetadata:
    """Reusable provenance record for a seed, fallback, or RAG prompt."""

    prompt_pack_id: str
    prompt_role: PromptRole
    model_lane: RagLane
    generated_prompt_id: str
    source_terms: tuple[str, ...]
    source_citation_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UserFollowupRequest:
    """Validated request metadata before orchestration resolves its parent target."""

    schema_version: str
    request_id: str
    selector_type: FollowupSelectorType
    selector_id: str
    followup_reason: str
    trigger_priority: int
    requester: str
    requested_at: str
    dry_run_only: bool
    budget_scope: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Reject incomplete user-request metadata at the shared boundary."""
        if self.schema_version != USER_FOLLOWUP_REQUEST_SCHEMA_VERSION:
            field = "schema_version"
            raise ContractValidationError(field, self.schema_version)
        for field_name, value in (
            ("request_id", self.request_id),
            ("selector_id", self.selector_id),
            ("followup_reason", self.followup_reason),
            ("requester", self.requester),
            ("requested_at", self.requested_at),
        ):
            if not value.strip():
                raise ContractValidationError(field_name, "must not be blank")
        if self.trigger_priority < 0:
            field = "trigger_priority"
            raise ContractValidationError(field, "must be non-negative")


@dataclass(frozen=True, slots=True)
class BudgetCounts:
    """Integer planned-work quantities used for budget approval decisions."""

    model_invocations: int
    sam2_calls: int
    tiles: int
    prompt_variants: int
    estimated_output_bytes: int
    smoke_images: int

    def __post_init__(self) -> None:
        """Reject negative planned quantities before they reach orchestration."""
        if min(
            self.model_invocations,
            self.sam2_calls,
            self.tiles,
            self.prompt_variants,
            self.estimated_output_bytes,
            self.smoke_images,
        ) < 0:
            field = "budget_counts"
            raise ContractValidationError(field, "must be non-negative")


@dataclass(frozen=True, slots=True)
class BudgetThresholds:
    """Locked approval-gate limits; these values never truncate planned work."""

    max_planned_model_invocations_without_approval: int = 500
    max_planned_sam2_calls_without_approval: int = 500
    max_planned_tiles_without_approval: int = 1000
    max_planned_prompt_variants_without_approval: int = 1500
    max_estimated_output_gb_without_approval: int = 5
    max_smoke_images_without_approval: int = 3

    def as_tuple(self) -> tuple[int, int, int, int, int, int]:
        """Return limits in the locked plan order."""
        return (
            self.max_planned_model_invocations_without_approval,
            self.max_planned_sam2_calls_without_approval,
            self.max_planned_tiles_without_approval,
            self.max_planned_prompt_variants_without_approval,
            self.max_estimated_output_gb_without_approval,
            self.max_smoke_images_without_approval,
        )

    def within_limits_counts(self) -> BudgetCounts:
        """Return a zero-work plan that never needs budget approval."""
        return BudgetCounts(0, 0, 0, 0, 0, 0)

    def exceeding_all_counts(self) -> BudgetCounts:
        """Return boundary-overflow values for deterministic approval tests."""
        return BudgetCounts(501, 501, 1001, 1501, (5 * 1024**3) + 1, 4)


BUDGET_THRESHOLDS = BudgetThresholds()


class BudgetThreshold(StrEnum):
    """Named budget dimensions recorded in approval artifacts."""

    MODEL_INVOCATIONS = "model_invocations"
    SAM2_CALLS = "sam2_calls"
    TILES = "tiles"
    PROMPT_VARIANTS = "prompt_variants"
    ESTIMATED_OUTPUT_GB = "estimated_output_gb"
    SMOKE_IMAGES = "smoke_images"
