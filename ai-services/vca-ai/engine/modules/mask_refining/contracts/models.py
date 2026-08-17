"""Typed visual-only Qwen evidence records."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from modules.mask_refining.contracts.morphology import morphology_descriptor_terms
from modules.rough_masking import AssetReference, RawDetectorCandidate
from modules.shared import (
    CANDIDATE_METADATA_BACKEND_ID,
    FIELD_ROLE_USAGE,
    OBSERVATION_POLICY_ID,
    OBSERVATION_POLICY_VERSION,
    PROMPT_ID,
    PROMPT_VERSION,
    QWEN_MODEL_ID,
    VOCABULARY_VERSION,
    BridgeFieldRole,
    BridgeFieldUsage,
    CandidateId,
    ContractValidationError,
    QwenBridgeResult,
    QwenBridgeStatus,
)

PADDING_PX: Final = 4
MAX_AREA_PX: Final = 1024
FOCUSED_MASK_OVERLAY_MODE: Final = "focused_mask_overlay"
MERGE_POLICY: Final = "masked_target_primary_with_bounded_context"
DEFAULT_SOURCE_WIDTH_PX: Final = 100
DEFAULT_SOURCE_HEIGHT_PX: Final = 80


def _raise_contract(field: str, reason: str) -> None:
    raise ContractValidationError(field, reason)


class QwenViewKind(StrEnum):
    """The two required visual-only Qwen input roles."""

    MASKED_TARGET_CROP = "masked_target_crop"
    BOUNDED_PADDED_CANDIDATE_CROP = "bounded_padded_candidate_crop"


class RendererExecutionKind(StrEnum):
    """Evidence class for the renderer seam."""

    INDEPENDENT_RAW_IMAGE = "independent_raw_image"
    TEST_FAKE = "test_fake"


class BackendExecutionKind(StrEnum):
    """Evidence class for the Qwen backend seam."""

    INDEPENDENT_RAW_IMAGE = "independent_raw_image"
    TEST_FAKE = "test_fake"
    SELECTED7 = "selected7"


class CacheStatus(StrEnum):
    """Cache accounting state retained with each observation."""

    HIT = "hit"
    MISS = "miss"


class EvidenceStage(StrEnum):
    """Pipeline stage that produced a visual evidence outcome."""

    ASSET_VALIDATION = "asset_validation"
    VIEW_RENDERING = "view_rendering"
    BACKEND_VALIDATION = "backend_validation"
    OBSERVATION_PARSING = "observation_parsing"
    FINALIZED = "finalized"


class EvidenceFailureCode(StrEnum):
    """Machine-readable fail-closed visual evidence failures."""

    PATH_ESCAPE = "path_escape"
    SOURCE_ASSET_MISSING = "source_asset_missing"
    SOURCE_HASH_MISMATCH = "source_hash_mismatch"
    MASK_ASSET_MISSING = "mask_asset_missing"
    MASK_HASH_MISMATCH = "mask_hash_mismatch"
    VIEW_ASSET_MISSING = "view_asset_missing"
    VIEW_HASH_MISMATCH = "view_hash_mismatch"
    VIEW_MEDIA_TYPE_INVALID = "view_media_type_invalid"
    VIEW_PNG_INVALID = "view_png_invalid"
    VIEW_CONTRACT_INVALID = "view_contract_invalid"
    INVALID_BBOX = "invalid_bbox"
    INVALID_MASK = "invalid_mask"
    CACHE_HASH_MISMATCH = "cache_hash_mismatch"
    UNSUPPORTED_DEVICE = "unsupported_device"
    RENDERER_NOT_INDEPENDENT = "renderer_not_independent"
    BACKEND_NOT_INDEPENDENT = "backend_not_independent"
    MALFORMED_OUTPUT = "malformed_output"
    MISSING_OUTPUT_FIELD = "missing_output_field"
    INVALID_CONFIDENCE = "invalid_confidence"
    INVALID_QUERY_FIELD = "invalid_query_field"
    IMAGE_DECODE_FAILED = "image_decode_failed"


@dataclass(frozen=True, slots=True)
class QwenInputView:
    """One renderer-created image asset passed to Qwen."""

    view_id: str
    kind: QwenViewKind
    asset_hash: str
    media_type: str
    relative_path: str
    call_order: int
    padding_px: int = PADDING_PX
    max_area_px: int = MAX_AREA_PX
    focused_mask_overlay_mode: str = FOCUSED_MASK_OVERLAY_MODE
    merge_policy: str = MERGE_POLICY
    prompt_id: str = PROMPT_ID
    prompt_version: str = PROMPT_VERSION
    candidate_metadata_backend_id: str = CANDIDATE_METADATA_BACKEND_ID
    crop_xyxy: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    rendered_width_px: int = 32
    rendered_height_px: int = 32

    def __post_init__(self) -> None:
        """Reject drift from the fixed visual evidence metadata."""
        if self.call_order not in (1, 2):
            _raise_contract("call_order", "must be one or two")
        if self.padding_px != PADDING_PX or self.max_area_px != MAX_AREA_PX:
            _raise_contract("view_geometry", "locked values required")
        if self.focused_mask_overlay_mode != FOCUSED_MASK_OVERLAY_MODE:
            _raise_contract(
                "focused_mask_overlay_mode",
                "locked mode required",
            )
        if self.merge_policy != MERGE_POLICY:
            _raise_contract("merge_policy", "locked policy required")
        if self.prompt_id != PROMPT_ID or self.prompt_version != PROMPT_VERSION:
            _raise_contract("prompt_metadata", "locked prompt required")
        rendered_area = self.rendered_width_px * self.rendered_height_px
        if self.rendered_width_px < 1 or self.rendered_height_px < 1:
            _raise_contract("rendered_dimensions_px", "must be positive")
        if rendered_area > self.max_area_px:
            _raise_contract("rendered_area_px", "area exceeds locked maximum")

    @property
    def asset_id(self) -> str:
        """Return the stable evidence identifier for this rendered asset."""
        return f"qwen-view:{self.view_id}"


@dataclass(frozen=True, slots=True)
class ViewRenderRequest:
    """Typed renderer input derived from one accepted detector candidate."""

    candidate: RawDetectorCandidate
    source_asset: AssetReference
    source_width_px: int
    source_height_px: int


@dataclass(frozen=True, slots=True)
class QwenRefinementRequest:
    """Assets and device selected for one candidate refinement attempt."""

    candidate: RawDetectorCandidate
    source_asset: AssetReference
    asset_root: Path
    device: str
    source_width_px: int = DEFAULT_SOURCE_WIDTH_PX
    source_height_px: int = DEFAULT_SOURCE_HEIGHT_PX

    def to_view_request(self) -> ViewRenderRequest:
        """Create the narrow renderer request without exposing Qwen backend state."""
        return ViewRenderRequest(
            self.candidate,
            self.source_asset,
            self.source_width_px,
            self.source_height_px,
        )


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    """Provenance available before an observation is parsed."""

    candidate_id: CandidateId | None
    input_views: tuple[QwenInputView, ...]
    cache_status: CacheStatus


@dataclass(frozen=True, slots=True)
class EvidenceFailure:
    """Structured reason for a failed visual evidence result."""

    code: EvidenceFailureCode
    reason: str
    stage: EvidenceStage


@dataclass(frozen=True, slots=True)
class QwenEvidenceResult:
    """A successful or explicit failed Qwen visual-only observation."""

    candidate_id: CandidateId | None
    input_views: tuple[QwenInputView, ...]
    input_view_ids: tuple[str, ...]
    input_view_hashes: tuple[str, ...]
    cache_status: CacheStatus
    observation_id: str | None
    observation_text: str
    selected_terms: tuple[str, ...]
    rejected_terms: tuple[str, ...]
    extracted_descriptors: tuple[str, ...]
    morphology: str
    confidence: float | None
    reason: str
    failure_code: EvidenceFailureCode | None
    failure_reason: str | None
    failed_stage: EvidenceStage
    final_success: bool
    report_display_text: str
    model_id: str = QWEN_MODEL_ID
    prompt_id: str = PROMPT_ID
    prompt_version: str = PROMPT_VERSION
    vocabulary_version: str = VOCABULARY_VERSION
    observation_policy_id: str = OBSERVATION_POLICY_ID
    observation_policy_version: str = OBSERVATION_POLICY_VERSION
    field_roles: Mapping[BridgeFieldRole, BridgeFieldUsage] = FIELD_ROLE_USAGE

    def to_bridge_result(self) -> QwenBridgeResult:
        """Convert local Qwen evidence into the shared C-004 bridge record."""
        candidate_id = self.candidate_id
        if candidate_id is None:
            field = "candidate_id"
            reason = "bridge result requires candidate id"
            raise ContractValidationError(field, reason)
        status = (
            QwenBridgeStatus.SUCCESS if self.final_success else QwenBridgeStatus.FAILED
        )
        failure_code = None if self.failure_code is None else self.failure_code.value
        extracted_descriptors = ()
        if self.final_success:
            extracted_descriptors = tuple(
                dict.fromkeys(
                    (
                        *self.extracted_descriptors,
                        *morphology_descriptor_terms(self.morphology),
                    )
                )
            )
        return QwenBridgeResult(
            candidate_id=candidate_id,
            status=status,
            selected_terms=self.selected_terms if self.final_success else (),
            extracted_descriptors=extracted_descriptors,
            confidence=self.confidence,
            reason=self.reason,
            qwen_observation_id=self.observation_id,
            input_view_hashes=self.input_view_hashes,
            failure_code=failure_code,
        )


def failure_result(
    context: EvidenceContext, failure: EvidenceFailure
) -> QwenEvidenceResult:
    """Build a non-null display-safe failed Qwen evidence record."""
    return QwenEvidenceResult(
        candidate_id=context.candidate_id,
        input_views=context.input_views,
        input_view_ids=tuple(view.view_id for view in context.input_views),
        input_view_hashes=tuple(view.asset_hash for view in context.input_views),
        cache_status=context.cache_status,
        observation_id=None,
        observation_text="",
        selected_terms=(),
        rejected_terms=(),
        extracted_descriptors=(),
        morphology="unknown",
        confidence=None,
        reason=failure.reason,
        failure_code=failure.code,
        failure_reason=failure.reason,
        failed_stage=failure.stage,
        final_success=False,
        report_display_text="없음",
    )
