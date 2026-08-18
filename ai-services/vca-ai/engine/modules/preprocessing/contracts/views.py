"""Immutable records for object and scale-aware preprocessing views."""

import json
from dataclasses import asdict, dataclass
from enum import StrEnum

from modules.shared import ContractValidationError, DetectorLane, ImageId

MIN_OBJECT_TILE_OVERLAP = 0.1


def _invalid(field: str, reason: str) -> ContractValidationError:
    return ContractValidationError(field, reason)


class ScaleConfidence(StrEnum):
    """Confidence level for a detected physical scale marker."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNAVAILABLE = "unavailable"


class ViewKind(StrEnum):
    """Supported preprocessing view roles."""

    FULL_IMAGE = "full_image"
    OBJECT_CROP = "object_crop"
    RANKED_OBJECT_TILE = "ranked_object_tile"
    RAG_FOLLOWUP = "rag_followup"


class ViewReuseMode(StrEnum):
    """Supported automatic follow-up view reuse semantics."""

    SOURCE_VIEW = "source_view"


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Original-image coordinate rectangle with a positive area."""

    left: float
    top: float
    width: float
    height: float

    def __post_init__(self) -> None:
        """Reject zero-area geometry before any view ID is computed."""
        if self.width <= 0 or self.height <= 0:
            field = "bbox"
            reason = "must have positive area"
            raise _invalid(field, reason)

    @property
    def area(self) -> float:
        """Return the rectangle area in original-image pixels."""
        return self.width * self.height


@dataclass(frozen=True, slots=True)
class ScaleMetadata:
    """D021 scale fields serialized unchanged on every planned view."""

    scale_marker_detected: bool
    scale_marker_bbox: BoundingBox | None
    scale_marker_width_px: float | None
    scale_unit_px: float | None
    scale_unit_source: str | None
    scale_confidence: ScaleConfidence
    confidence_reasons: tuple[str, ...]
    fallback_reason: str | None
    scale_unit_label: str | None = None
    scale_unit_value: float | None = None
    scale_marker_orientation: str | None = None
    scale_recognition_method: str = "manual_or_fixture"

    def __post_init__(self) -> None:
        """Keep scale marker and confidence states internally consistent."""
        if not self.confidence_reasons:
            field = "confidence_reasons"
            reason = "must not be empty"
            raise _invalid(field, reason)
        match self.scale_marker_detected:
            case True:
                if (
                    self.scale_marker_bbox is None
                    or self.scale_marker_width_px is None
                    or self.scale_marker_width_px <= 0
                    or self.scale_unit_px is None
                    or self.scale_unit_px <= 0
                    or self.scale_unit_source is None
                    or not self.scale_unit_source.strip()
                ):
                    field = "scale_marker"
                    reason = "detected marker is invalid"
                    raise _invalid(field, reason)
            case False:
                if any(
                    value is not None
                    for value in (
                        self.scale_marker_bbox,
                        self.scale_marker_width_px,
                        self.scale_unit_px,
                        self.scale_unit_source,
                    )
                ):
                    field = "scale_marker"
                    reason = "undetected marker has values"
                    raise _invalid(field, reason)


@dataclass(frozen=True, slots=True)
class ObjectRankingHints:
    """Upstream visual signals used to rank tiles rather than scan order."""

    local_texture_variance: float
    local_color_variance: float
    candidate_uncertainty: float
    candidate_scarcity: float

    def __post_init__(self) -> None:
        """Require normalized ranking hints from the typed planner boundary."""
        if any(value < 0 or value > 1 for value in self._values()):
            field = "ranking_hints"
            reason = "values must be between zero and one"
            raise _invalid(field, reason)

    def _values(self) -> tuple[float, float, float, float]:
        return (
            self.local_texture_variance,
            self.local_color_variance,
            self.candidate_uncertainty,
            self.candidate_scarcity,
        )


@dataclass(frozen=True, slots=True)
class ObjectTarget:
    """Object geometry and ranked-tile inputs for one image."""

    bbox: BoundingBox
    ranking_hints: ObjectRankingHints


@dataclass(frozen=True, slots=True)
class CoordinateTransform:
    """Explicit original-coordinate restoration metadata for a cropped view."""

    source_bbox: BoundingBox
    restore_offset_x: float
    restore_offset_y: float
    original_to_view_scale_x: float = 1.0
    original_to_view_scale_y: float = 1.0
    coordinate_transform_version: str = "original-coordinate-restore-v1"


@dataclass(frozen=True, slots=True)
class TileRankingMetadata:
    """Independent D010 tile signals and post-ranking diversity rationale."""

    object_mask_coverage: float
    object_boundary_overlap: float
    local_texture_variance: float
    local_color_variance: float
    uncovered_area_bonus: float
    candidate_uncertainty: float
    candidate_scarcity: float
    spatial_diversity_reasons: tuple[str, ...]
    rank_score: float

    def __post_init__(self) -> None:
        """Reject candidates that cannot meaningfully cover their target object."""
        if self.object_mask_coverage < MIN_OBJECT_TILE_OVERLAP:
            field = "object_mask_coverage"
            reason = "must satisfy minimum tile overlap"
            raise _invalid(field, reason)
        if not self.spatial_diversity_reasons:
            field = "spatial_diversity_reasons"
            reason = "must not be empty"
            raise _invalid(field, reason)


@dataclass(frozen=True, slots=True)
class ViewRecord:
    """One complete full, crop, tile, or follow-up view contract."""

    view_id: str
    kind: ViewKind
    image_id: ImageId
    object_id: str | None
    tile_view_id: str | None
    source_view_id: str | None
    rag_followup_view_id: str | None
    view_reuse_mode: ViewReuseMode | None
    coordinate_transform: CoordinateTransform | None
    scale_metadata: ScaleMetadata
    lane: DetectorLane | None = None
    tile_ranking: TileRankingMetadata | None = None

    def __post_init__(self) -> None:
        """Require restore metadata for every non-full view role."""
        match self.kind:
            case ViewKind.FULL_IMAGE:
                if self.coordinate_transform is not None:
                    field = "coordinate_transform"
                    reason = "full view has none"
                    raise _invalid(field, reason)
            case (
                ViewKind.OBJECT_CROP
                | ViewKind.RANKED_OBJECT_TILE
                | ViewKind.RAG_FOLLOWUP
            ):
                if self.coordinate_transform is None:
                    field = "coordinate_transform"
                    reason = "non-full view requires restore metadata"
                    raise _invalid(field, reason)


@dataclass(frozen=True, slots=True)
class LaneTilePlan:
    """Per-object/per-lane tiling history and untruncated dry-run estimate."""

    object_id: str
    lane: DetectorLane
    span_halving_history: tuple[float, ...]
    target_tile_count: int
    overlap_ratio: float
    dry_run_tile_count: int
    tiling_strategy: str


@dataclass(frozen=True, slots=True)
class ViewPlanningRequest:
    """Typed planner input independent from detector or model execution."""

    image_id: ImageId
    image_width_px: int
    image_height_px: int
    objects: tuple[ObjectTarget, ...]
    scale_metadata: ScaleMetadata
    detector_lanes: tuple[DetectorLane, ...] = (
        DetectorLane.OWLV2_SAM2,
        DetectorLane.GROUNDED_SAM2,
    )

    def __post_init__(self) -> None:
        """Reject invalid image dimensions before planning coordinate transforms."""
        if self.image_width_px <= 0 or self.image_height_px <= 0:
            field = "image_dimensions"
            reason = "must be positive"
            raise _invalid(field, reason)


@dataclass(frozen=True, slots=True)
class ViewManifest:
    """Serializable planned views and untruncated tile approval signal."""

    image_id: ImageId
    full_views: tuple[ViewRecord, ...]
    object_views: tuple[ViewRecord, ...]
    tile_views: tuple[ViewRecord, ...]
    rag_followup_views: tuple[ViewRecord, ...]
    lane_plans: tuple[LaneTilePlan, ...]
    dry_run_tile_count: int
    requires_user_budget_approval: bool

    def to_json(self) -> str:
        """Serialize nested records, including all exact D021 field names."""
        return json.dumps(
            asdict(self), ensure_ascii=True, separators=(",", ":"), sort_keys=True
        )
