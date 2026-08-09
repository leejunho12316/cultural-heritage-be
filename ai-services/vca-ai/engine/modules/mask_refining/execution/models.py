"""Lightweight contracts for prompt-driven mask refinement execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from modules.preprocessing import ViewRecord
    from modules.prompt_generating import PromptRecord, PromptVariant
    from modules.rough_masking import DetectorRunner, RawDetectorCandidate
    from modules.rough_masking.artifacts.assets import AssetReference
    from modules.shared import DetectorLane, RagLane


class RefinementStatus(StrEnum):
    """Terminal outcome for one parent and model-lane prompt group."""

    EXECUTED = "executed"
    FAILED = "failed"


class SkipStage(StrEnum):
    """Pipeline boundary that could not produce a refinement execution."""

    PROMPT_INPUT = "prompt_input"
    ASSET_JOIN = "asset_join"
    EXECUTION = "execution"


@dataclass(frozen=True, slots=True)
class RefinementSkip:
    """A non-fatal malformed input or unavailable execution dependency."""

    stage: SkipStage
    reason: str
    line_number: int | None = None
    rag_parent_candidate_id: str | None = None
    model_lane: RagLane | None = None


@dataclass(frozen=True, slots=True)
class PromptVariantGroup:
    """All prompt variants targeting one rough candidate through one RAG lane."""

    rag_parent_candidate_id: str
    model_lane: RagLane
    variants: tuple[PromptVariant, ...]


@dataclass(frozen=True, slots=True)
class PromptVariantReadResult:
    """Parsed prompt artifact manifest, valid groups, and malformed-row skips."""

    manifest_schema: str
    groups: tuple[PromptVariantGroup, ...]
    skips: tuple[RefinementSkip, ...]


@dataclass(frozen=True, slots=True)
class JoinedRefinementAssets:
    """Object ROI assets and typed view prepared for one refinement group."""

    object_id: str
    roi_image_path: Path
    object_mask_path: Path
    image_width_px: int
    image_height_px: int
    view: ViewRecord
    original_image_width_px: int | None = None
    original_image_height_px: int | None = None


@dataclass(frozen=True, slots=True)
class RefinementRunRequest:
    """CLI-owned paths and local runtime policy for one refinement run."""

    prompt_output_dir: Path
    rough_root: Path
    asset_root: Path
    output_dir: Path
    model_cache_root: Path
    device: str
    verify_model_hashes: bool
    max_groups: int | None = None


@dataclass(frozen=True, slots=True)
class RunnerFactoryInput:
    """All values required to build one local detector and SAM2 runner."""

    lane: DetectorLane
    image_path: Path
    model_cache_root: Path
    device: str
    verify_model_hashes: bool


class RefinementRunnerFactory(Protocol):
    """Builds a runner at the narrow inference boundary."""

    def __call__(self, details: RunnerFactoryInput) -> DetectorRunner:
        """Return the runner for one ROI image and active detector lane."""
        ...


@dataclass(frozen=True, slots=True)
class PostRefinementQwenEvidence:
    """Report-safe post-refinement Qwen observation for one accepted candidate."""

    final_success: bool
    report_display_text: str
    confidence: float | None


class PostRefinementQwenEvidenceFactory(Protocol):
    """Produces post-refinement Qwen evidence for one refined candidate."""

    def __call__(
        self,
        candidate: RawDetectorCandidate,
        source_asset: AssetReference | None,
        assets: JoinedRefinementAssets,
        lane_output_dir: Path,
    ) -> PostRefinementQwenEvidence:
        """Return non-null evidence; never raises for a missing source asset."""
        ...


@dataclass(frozen=True, slots=True)
class AcceptedRefinedCandidate:
    """Accepted refined mask candidate fields exported to startup consumers.

    `mask` is the refined SAM2 mask restored to original-image pixel space
    (see runner._restore_original_mask) - it is the mask anomaly_grouping
    should use as ground truth. It is None only when the original image
    dimensions were unavailable and restoration could not be performed; that
    case is a rough_masking mask fallback for the startup layer to handle,
    not a mask_refining concern.
    """

    candidate_id: str
    image_id: str
    source_object_id: str
    source_view_id: str
    bbox_xyxy: tuple[float, float, float, float]
    original_bbox_xyxy: tuple[float, float, float, float]
    prompt: str
    qwen_final_success: bool
    qwen_report_display_text: str
    qwen_confidence: float | None
    mask: AssetReference | None = None


@dataclass(frozen=True, slots=True)
class RefinedExecutionRecord:
    """Serializable result of one executed or failed prompt group."""

    rag_parent_candidate_id: str
    model_lane: RagLane
    detector_lane: DetectorLane
    prompt_records: tuple[PromptRecord, ...]
    records_json: Path
    status: RefinementStatus
    diagnostics: tuple[str, ...]
    accepted_candidate_ids: tuple[str, ...]
    accepted_candidates: tuple[AcceptedRefinedCandidate, ...]


@dataclass(frozen=True, slots=True)
class RefinementRunResult:
    """Run-level accounting returned to the CLI after artifact materialization."""

    records: tuple[RefinedExecutionRecord, ...]
    skips: tuple[RefinementSkip, ...]

    @property
    def executed_groups(self) -> int:
        """Return the number of groups whose runner completed."""
        return sum(
            record.status is RefinementStatus.EXECUTED for record in self.records
        )
