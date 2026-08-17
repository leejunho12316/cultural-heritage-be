from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

from modules.mask_refining import (
    CANDIDATE_METADATA_BACKEND_ID,
    PROMPT_ID,
    PROMPT_VERSION,
    QWEN_BACKEND_DEPENDENCY,
    QWEN_BACKEND_KIND,
    QWEN_MODEL_ID,
    VOCABULARY_VERSION,
    AssetReference,
    BackendExecutionKind,
    BackendResponse,
    CacheStatus,
    QwenBackendRequest,
    QwenInputView,
    QwenViewKind,
    RendererExecutionKind,
    ViewRenderRequest,
    cache_input_views,
)
from modules.rough_masking import CandidateStatus, RawDetectorCandidate, SeedThresholds
from modules.shared import (
    CandidateId,
    DetectorLane,
    ImageId,
    PromptMetadata,
    PromptRole,
    RagLane,
)

if TYPE_CHECKING:
    from pathlib import Path

PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def make_asset(
    root: Path, relative_path: str, contents: bytes, media_type: str
) -> AssetReference:
    """Create a fixture asset with a matching content hash."""
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(contents)
    return AssetReference(relative_path, sha256(contents).hexdigest(), media_type)


def make_candidate(root: Path) -> tuple[RawDetectorCandidate, AssetReference]:
    """Create one accepted rough candidate and its source image fixture."""
    source = make_asset(root, "source.jpg", b"\xff\xd8source", "image/jpeg")
    mask = make_asset(root, "masks/candidate.png", PNG_HEADER + b"mask", "image/png")
    overlay = make_asset(
        root,
        "overlays/candidate.jpg",
        b"\xff\xd8overlay",
        "image/jpeg",
    )
    candidate = RawDetectorCandidate(
        candidate_id=CandidateId("candidate-001"),
        image_id=ImageId("image-001"),
        lane=DetectorLane.OWLV2_SAM2,
        status=CandidateStatus.ACCEPTED,
        executable_prompt="surface variation",
        score=0.8,
        bbox_xyxy=(10.0, 12.0, 30.0, 32.0),
        rough_mask=mask,
        overlay=overlay,
        detector_model_id="detector-v1",
        sam2_model_id="sam2-v1",
        threshold_config=SeedThresholds(0.1, None, 2, 0.3),
        prompt_provenance=PromptMetadata(
            prompt_pack_id="seed-pack",
            prompt_role=PromptRole.STATIC_SEED,
            model_lane=RagLane.OWLV2,
            generated_prompt_id="seed-001",
            source_terms=("surface variation",),
        ),
        source_view_id="source-view-001",
        source_object_id=None,
        source_tile_view_id=None,
        diagnostics=(),
    )
    return candidate, source


def valid_observation_json() -> str:
    """Return a valid one-record Qwen JSON response fixture."""
    return (
        '{"observation_id":"observation-001",'
        '"observation_text":"A small irregular brown region is visible.",'
        '"selected_terms":["brown region"],'
        '"rejected_terms":["linear mark"],'
        '"extracted_descriptors":["irregular","brown"],'
        '"morphology":"spot",'
        '"confidence":0.84,'
        '"reason":"Both input views show the same localized visual feature."}'
    )


@dataclass(frozen=True, slots=True)
class IndependentRenderer:
    """Production-like renderer fixture for tests."""

    root: Path
    execution_kind: RendererExecutionKind = RendererExecutionKind.INDEPENDENT_RAW_IMAGE
    renderer_kind: str = QWEN_BACKEND_KIND
    renderer_dependency: str = CANDIDATE_METADATA_BACKEND_ID

    def render(self, request: ViewRenderRequest) -> tuple[QwenInputView, ...]:
        """Create both required Qwen input view fixtures."""
        _ = request
        masked = make_asset(
            self.root,
            "views/masked-target.png",
            PNG_HEADER + b"masked-target",
            "image/png",
        )
        bounded = make_asset(
            self.root,
            "views/bounded-padded.png",
            PNG_HEADER + b"bounded-padded",
            "image/png",
        )
        return (
            QwenInputView(
                view_id="candidate-001:masked",
                kind=QwenViewKind.MASKED_TARGET_CROP,
                asset_hash=masked.sha256,
                media_type=masked.media_type,
                relative_path=masked.relative_path,
                call_order=1,
            ),
            QwenInputView(
                view_id="candidate-001:bounded",
                kind=QwenViewKind.BOUNDED_PADDED_CANDIDATE_CROP,
                asset_hash=bounded.sha256,
                media_type=bounded.media_type,
                relative_path=bounded.relative_path,
                call_order=2,
            ),
        )


@dataclass(frozen=True, slots=True)
class FakeRenderer(IndependentRenderer):
    """Test-only renderer fixture that must not produce final success."""

    execution_kind: RendererExecutionKind = RendererExecutionKind.TEST_FAKE


@dataclass(frozen=True, slots=True)
class IndependentBackend:
    """Production-like backend fixture for tests."""

    raw_output: str
    cache_hash: str | None = None
    execution_kind: BackendExecutionKind = BackendExecutionKind.INDEPENDENT_RAW_IMAGE
    backend_kind: str = QWEN_BACKEND_KIND
    backend_dependency: str = QWEN_BACKEND_DEPENDENCY
    model_id: str = QWEN_MODEL_ID
    device: str = "cpu"
    cache_status: CacheStatus = CacheStatus.MISS

    def observe(self, request: QwenBackendRequest) -> BackendResponse:
        """Return configured raw output and cache evidence."""
        return BackendResponse(
            raw_output=self.raw_output,
            cache_status=self.cache_status,
            cache_hash=(
                cache_input_views(request.input_views)
                if self.cache_hash is None
                else self.cache_hash
            ),
        )


@dataclass(frozen=True, slots=True)
class FakeBackend(IndependentBackend):
    """Test-only backend fixture that must not produce final success."""

    execution_kind: BackendExecutionKind = BackendExecutionKind.TEST_FAKE


@dataclass(frozen=True, slots=True)
class Selected7Backend(IndependentBackend):
    """Legacy selected7 backend fixture that must fail closed."""

    execution_kind: BackendExecutionKind = BackendExecutionKind.SELECTED7
    backend_kind: str = "selected7"


def required_prompt_metadata() -> tuple[str, str, str]:
    """Return the prompt metadata constants expected by Qwen views."""
    return PROMPT_ID, PROMPT_VERSION, VOCABULARY_VERSION
