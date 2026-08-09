"""Typed records emitted by real preprocessing materialization."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Final, TypeVar

from modules.shared import BUDGET_THRESHOLDS

if TYPE_CHECKING:
    from modules.preprocessing.contracts.views import BoundingBox, ScaleMetadata
    from modules.prompt_generating import PromptRecord
    from modules.shared import DetectorLane

TProcessor = TypeVar("TProcessor")
TModel = TypeVar("TModel")
OWLV2_NATIVE_INPUT_SIZE: Final = 960
DEFAULT_DETECTOR_INPUT_SIZE: Final = 1152
LARGE_DETECTOR_INPUT_SIZE: Final = 1344
MEDIUM_DETECTOR_LONG_SIDE_PX: Final = 2400
LARGE_DETECTOR_LONG_SIDE_PX: Final = 5000


def detector_input_size_for_image(width: int, height: int) -> int:
    """Choose an OWLv2 square input size from source-image dimensions."""
    long_side = max(width, height)
    if long_side <= MEDIUM_DETECTOR_LONG_SIDE_PX:
        return OWLV2_NATIVE_INPUT_SIZE
    if long_side <= LARGE_DETECTOR_LONG_SIDE_PX:
        return DEFAULT_DETECTOR_INPUT_SIZE
    return LARGE_DETECTOR_INPUT_SIZE


@dataclass(frozen=True, slots=True)
class DetectionBox:
    """One detector bounding box in source-image coordinates."""

    x0: float
    y0: float
    x1: float
    y1: float
    score: float
    prompt_text: str
    generated_prompt_id: str


@dataclass(frozen=True, slots=True)
class DetectionRun[TProcessor, TModel]:
    """Inputs required to run one deferred detector pass."""

    processor: TProcessor
    model: TModel
    prompts: tuple[PromptRecord, ...]
    threshold: float
    max_detections: int | None
    detector_input_size: int


@dataclass(frozen=True, slots=True)
class MaterializedAssetRecord:
    """Integrity metadata for one preprocessing materialized asset."""

    path: str
    sha256: str
    media_type: str


@dataclass(frozen=True, slots=True)
class ObjectAssetRecord:
    """Output asset paths for one detected object."""

    candidate_id: str
    object_id: str
    image_id: str
    lane: DetectorLane
    accepted: bool
    diagnostics: tuple[str, ...]
    bbox_xyxy: tuple[float, float, float, float]
    score: float
    sam2_score: float
    prompt_pack_id: str
    prompt_role: str
    prompt_text: str
    generated_prompt_id: str
    source_terms: tuple[str, ...]
    detector_model_id: str
    detector_model_revision: str
    sam2_model_id: str
    sam2_model_revision: str
    device: str
    source_image_sha256: str
    detector_input_sha256: str
    scale_metadata: ScaleMetadata
    scale_removal_applied: bool
    mask: MaterializedAssetRecord
    bbox_crop: MaterializedAssetRecord
    alpha_cutout: MaterializedAssetRecord
    detection_overlay: MaterializedAssetRecord
    tile: MaterializedAssetRecord
    tiles: tuple[MaterializedAssetRecord, ...]
    # tiles[i]의 원본 이미지 좌표계 bbox(xyxy) - bbox_xyxy와 같은 좌표
    # 프레임. rough_masking이 타일별로 탐지를 라우팅하고 좌표를 복원하려면
    # 타일 파일 경로뿐 아니라 이 bbox가 반드시 있어야 한다.
    tile_bboxes: tuple[tuple[float, float, float, float], ...]


@dataclass(frozen=True, slots=True)
class RealImageRecord:
    """Image-level preprocessing metadata for one real-run input."""

    image_id: str
    source_image_sha256: str
    detector_input_sha256: str
    scale_metadata: ScaleMetadata
    scale_removal_applied: bool
    scale_removal_mode: str
    scale_removal_bbox: BoundingBox | None
    detector_input: MaterializedAssetRecord


@dataclass(frozen=True, slots=True)
class RealPreprocessingManifest:
    """Manifest for model-backed preprocessing assets."""

    schema_version: str
    model_inventory: str
    device: str
    detector_lane_status: str
    model_invocations: int
    sam2_calls: int
    manifest_id: str
    processed_image_count: int
    object_count: int
    images: tuple[RealImageRecord, ...]
    objects: tuple[ObjectAssetRecord, ...]

    def to_jsonable(self) -> dict[str, str | int | bool | list[dict[str, object]]]:
        """Convert the manifest into the public JSON shape."""
        tile_count = sum(len(record.tiles) for record in self.objects)
        return {
            "schema_version": self.schema_version,
            "model_inventory": self.model_inventory,
            "device": self.device,
            "detector_lane_status": self.detector_lane_status,
            "model_invocations": self.model_invocations,
            "sam2_calls": self.sam2_calls,
            "manifest_id": self.manifest_id,
            "processed_image_count": self.processed_image_count,
            "object_count": self.object_count,
            "tile_count": tile_count,
            "tile_budget_limit": BUDGET_THRESHOLDS.max_planned_tiles_without_approval,
            "requires_user_budget_approval": tile_count
            > BUDGET_THRESHOLDS.max_planned_tiles_without_approval,
            "images": [asdict(record) for record in self.images],
            "objects": [asdict(record) for record in self.objects],
        }
