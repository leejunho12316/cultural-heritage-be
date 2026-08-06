"""Typed canonical hash inputs and excluded runtime metadata."""

from dataclasses import dataclass
from pathlib import PurePath

from modules.shared.errors import ContractValidationError
from modules.shared.lanes import RagLane
from modules.shared.models import (
    BudgetCounts,
    BudgetThreshold,
    CandidateId,
    DryRunId,
    FollowupHash,
    ImageId,
    PlannedCountHash,
    SourceImageManifestHash,
)


@dataclass(frozen=True, slots=True)
class SourceImageManifestItem:
    """One source-manifest pair eligible for canonical hashing."""

    relative_image_path: str
    file_content_hash: str

    def __post_init__(self) -> None:
        """Reject absolute or parent-traversal paths from manifest hash input."""
        path = PurePath(self.relative_image_path)
        if not self.relative_image_path or path.is_absolute() or ".." in path.parts:
            field = "relative_image_path"
            raise ContractValidationError(field, "must be contained and relative")


@dataclass(frozen=True, slots=True)
class RuntimeMetadata:
    """Operational values intentionally excluded from canonical planning hashes."""

    recorded_at: str
    output_dir: str
    hostname: str
    temporary_dir: str
    process_id: int


@dataclass(frozen=True, slots=True)
class LaneConfigVersion:
    """Versioned configuration for one active RAG lane."""

    lane: RagLane
    version: str


@dataclass(frozen=True, slots=True)
class DryRunHashInput:
    """Exact canonical inputs for a dry-run identity."""

    image_subset_ids: tuple[ImageId, ...]
    source_image_manifest_sha256: SourceImageManifestHash
    followup_request_hash: FollowupHash
    active_rag_lanes: tuple[RagLane, ...]
    corpus_id: str
    prompt_pack_ids: tuple[str, ...]
    threshold_cap_config_version: str
    tile_view_planner_config_version: str
    coordinate_transform_version: str
    active_rag_lane_config_versions: tuple[LaneConfigVersion, ...]
    runtime_metadata: RuntimeMetadata


@dataclass(frozen=True, slots=True)
class PlannedCountHashInput:
    """Exact canonical inputs for a planned-count hash."""

    dry_run_id: DryRunId
    image_subset_ids: tuple[ImageId, ...]
    followup_request_hash: FollowupHash
    counts: BudgetCounts
    exceeded_thresholds: tuple[BudgetThreshold, ...]
    threshold_cap_config_version: str
    runtime_metadata: RuntimeMetadata


@dataclass(frozen=True, slots=True)
class ReopenRequestHashInput:
    """Exact canonical inputs for a reopened-RAG request hash."""

    dry_run_id: DryRunId
    followup_request_hash: FollowupHash
    planned_count_hash: PlannedCountHash
    reopened_candidate_ids: tuple[CandidateId, ...]
    initial_planned_counts: BudgetCounts
    reopen_incremental_counts: BudgetCounts
    combined_planned_counts: BudgetCounts
    exceeded_thresholds: tuple[BudgetThreshold, ...]
    threshold_cap_config_version: str
    runtime_metadata: RuntimeMetadata
