"""Active detector/SAM2 adapter contracts and locked seed configuration."""

from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from modules.preprocessing import ViewRecord
from modules.preprocessing.contracts.views import ViewKind
from modules.prompt_generating import (
    STATIC_SEED_MINIMAL_PACK_ID,
    PromptPack,
    PromptRecord,
    static_seed_minimal_pack,
)
from modules.shared import (
    DETECTOR_ADAPTER_SCHEMA_VERSION,
    ContractValidationError,
    DetectorLane,
    PromptRole,
    detector_to_rag_lane,
    validate_active_detector_lanes,
)


@dataclass(frozen=True, slots=True)
class SeedThresholds:
    """Detector-only seed thresholds, deliberately separate from RAG thresholds."""

    box_threshold: float | None
    text_threshold: float | None
    max_boxes_per_prompt: int
    max_mask_area_ratio: float


@dataclass(frozen=True, slots=True)
class ImageDimensions:
    """Positive pixel dimensions for the source view image."""

    width_px: int
    height_px: int


@dataclass(frozen=True, slots=True)
class SeedRequestPaths:
    """Output paths owned by one active detector lane execution."""

    lane_output_dir: Path
    records_json: Path
    object_mask_path: Path | None = None


_SEED_THRESHOLDS: Final[dict[DetectorLane, SeedThresholds]] = {
    DetectorLane.OWLV2_SAM2: SeedThresholds(0.08, None, 2, 0.30),
    DetectorLane.GROUNDED_SAM2: SeedThresholds(0.25, 0.25, 2, 0.35),
    DetectorLane.FLORENCE2_SAM2: SeedThresholds(None, None, 1, 0.40),
}
_MODEL_IDS: Final[dict[DetectorLane, str]] = {
    DetectorLane.OWLV2_SAM2: "google/owlv2-base-patch16-ensemble",
    DetectorLane.GROUNDED_SAM2: "IDEA-Research/grounding-dino-base",
    DetectorLane.FLORENCE2_SAM2: "microsoft/Florence-2-base",
}
SAM2_MODEL_ID: Final = "facebook/sam2-hiera-large"


def seed_thresholds(lane: DetectorLane) -> SeedThresholds:
    """Return the locked detector-seed threshold family for one active lane."""
    _ = validate_active_detector_lanes((lane,))
    return _SEED_THRESHOLDS[lane]


# 전달된 prompt_pack이 유일하게 허용된 locked seed 팩(static_seed_minimal_pack)과
# 정확히 일치하는지 검증한다. build_seed_request의 첫 단계로, drift를 막는다.
def _validate_seed_pack(prompt_pack: PromptPack) -> None:
    if prompt_pack.prompt_pack_id != STATIC_SEED_MINIMAL_PACK_ID:
        field = "prompt_pack_id"
        reason = "locked seed pack required"
        raise ContractValidationError(field, reason)
    if prompt_pack.prompt_role is not PromptRole.STATIC_SEED:
        field = "prompt_role"
        reason = "static_seed required"
        raise ContractValidationError(field, reason)
    if prompt_pack.records != static_seed_minimal_pack.records:
        field = "prompt_records"
        reason = "locked seed records drifted"
        raise ContractValidationError(field, reason)


@dataclass(frozen=True, slots=True)
class AdapterRequest:
    """One active-lane request consumed by a detector/SAM2 runner."""

    schema_version: str
    lane: DetectorLane
    view: ViewRecord
    image_width_px: int
    image_height_px: int
    lane_output_dir: Path
    records_json: Path
    prompts: tuple[PromptRecord, ...]
    threshold_config: SeedThresholds
    detector_model_id: str
    sam2_model_id: str
    object_mask_path: Path | None = None

    def __post_init__(self) -> None:
        """Reject excluded lanes, invalid dimensions, and mismatched view identity."""
        _ = validate_active_detector_lanes((self.lane,))
        if self.schema_version != DETECTOR_ADAPTER_SCHEMA_VERSION:
            field = "schema_version"
            raise ContractValidationError(field, self.schema_version)
        if self.image_width_px <= 0 or self.image_height_px <= 0:
            field = "image_dimensions_invalid"
            reason = "must be positive"
            raise ContractValidationError(field, reason)
        if not self.prompts:
            field = "seed_prompts"
            reason = "missing active seed records"
            raise ContractValidationError(field, reason)
        expected_lane = detector_to_rag_lane(self.lane)
        if any(
            record.metadata.model_lane is not expected_lane for record in self.prompts
        ):
            field = "seed_prompts"
            reason = "wrong lane ownership"
            raise ContractValidationError(field, reason)
        if self.threshold_config != seed_thresholds(self.lane):
            field = "threshold_config"
            reason = "must be detector seed thresholds"
            raise ContractValidationError(field, reason)


@dataclass(frozen=True, slots=True)
class RunnerOutcome:
    """Explicit runner-invocation evidence returned through the mockable seam."""

    runner_invoked: bool


class DetectorRunner(Protocol):
    """Narrow runner seam for production subprocesses and test fakes."""

    def __call__(self, request: AdapterRequest) -> RunnerOutcome:
        """Run one active detector request and return invocation evidence."""
        ...


def build_seed_request(
    lane: DetectorLane,
    view: ViewRecord,
    image_dimensions: ImageDimensions,
    *,
    paths: SeedRequestPaths,
    prompt_pack: PromptPack,
) -> AdapterRequest:
    """Build an active request from the only allowed seed prompt-pack export."""
    _validate_seed_pack(prompt_pack)
    rag_lane = detector_to_rag_lane(lane)
    prompts = tuple(
        record
        for record in prompt_pack.records
        if record.metadata.model_lane is rag_lane
    )
    return AdapterRequest(
        DETECTOR_ADAPTER_SCHEMA_VERSION,
        lane,
        view,
        image_dimensions.width_px,
        image_dimensions.height_px,
        paths.lane_output_dir,
        paths.records_json,
        prompts,
        seed_thresholds(lane),
        _MODEL_IDS[lane],
        SAM2_MODEL_ID,
    )


def build_roi_seed_request(
    lane: DetectorLane,
    view: ViewRecord,
    image_dimensions: ImageDimensions,
    *,
    paths: SeedRequestPaths,
    prompt_pack: PromptPack = static_seed_minimal_pack,
) -> AdapterRequest:
    """Build an active request scoped to one preprocessing object ROI."""
    if view.kind not in (ViewKind.OBJECT_CROP, ViewKind.RANKED_OBJECT_TILE):
        field = "roi_view_kind"
        reason = "object ROI view required"
        raise ContractValidationError(field, reason)
    if view.object_id is None:
        field = "object_id"
        reason = "object ROI id required"
        raise ContractValidationError(field, reason)
    if view.coordinate_transform is None:
        field = "coordinate_transform"
        reason = "object ROI transform required"
        raise ContractValidationError(field, reason)
    if view.kind is ViewKind.RANKED_OBJECT_TILE and view.lane is not lane:
        field = "tile_lane"
        reason = "ranked tile lane must match request lane"
        raise ContractValidationError(field, reason)
    if paths.object_mask_path is None or not paths.object_mask_path.is_file():
        field = "object_mask_path"
        reason = "preprocessing object mask file required"
        raise ContractValidationError(field, reason)
    request = build_seed_request(
        lane,
        view,
        image_dimensions,
        paths=paths,
        prompt_pack=prompt_pack,
    )
    return AdapterRequest(
        request.schema_version,
        request.lane,
        request.view,
        request.image_width_px,
        request.image_height_px,
        request.lane_output_dir,
        request.records_json,
        request.prompts,
        request.threshold_config,
        request.detector_model_id,
        request.sam2_model_id,
        paths.object_mask_path,
    )
