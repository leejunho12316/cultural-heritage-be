"""Visual-only Qwen evidence contracts for accepted rough-mask candidates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from modules.mask_refining.contracts.models import (
    BackendExecutionKind,
    CacheStatus,
    EvidenceFailureCode,
    QwenEvidenceResult,
    QwenInputView,
    QwenRefinementRequest,
    QwenViewKind,
    RendererExecutionKind,
    ViewRenderRequest,
)
from modules.mask_refining.evidence.observation_parser import parse_observation
from modules.mask_refining.evidence.refinement import refine_candidate
from modules.mask_refining.rendering.views import FileQwenViewRenderer
from modules.mask_refining.runtime.backend import (
    BackendResponse,
    QwenBackend,
    QwenBackendRequest,
    cache_input_views,
)
from modules.mask_refining.runtime.transformers_backend import (
    TransformersQwenBackend,
    load_transformers_qwen_backend,
)
from modules.mask_refining.runtime.transformers_types import QwenRuntime
from modules.rough_masking import AssetReference
from modules.shared.constants import (
    CANDIDATE_METADATA_BACKEND_ID,
    OBSERVATION_POLICY_ID,
    OBSERVATION_POLICY_VERSION,
    PROMPT_ID,
    PROMPT_VERSION,
    QWEN_BACKEND_DEPENDENCY,
    QWEN_BACKEND_KIND,
    QWEN_MODEL_ID,
    VOCABULARY_VERSION,
)

if TYPE_CHECKING:
    from modules.mask_refining.rendering.production_views import PillowQwenViewRenderer

__all__ = (
    "CANDIDATE_METADATA_BACKEND_ID",
    "OBSERVATION_POLICY_ID",
    "OBSERVATION_POLICY_VERSION",
    "PROMPT_ID",
    "PROMPT_VERSION",
    "QWEN_BACKEND_DEPENDENCY",
    "QWEN_BACKEND_KIND",
    "QWEN_MODEL_ID",
    "VOCABULARY_VERSION",
    "AssetReference",
    "BackendExecutionKind",
    "BackendResponse",
    "CacheStatus",
    "EvidenceFailureCode",
    "FileQwenViewRenderer",
    "PillowQwenViewRenderer",
    "QwenBackend",
    "QwenBackendRequest",
    "QwenEvidenceResult",
    "QwenInputView",
    "QwenRefinementRequest",
    "QwenRuntime",
    "QwenViewKind",
    "RendererExecutionKind",
    "TransformersQwenBackend",
    "ViewRenderRequest",
    "cache_input_views",
    "load_transformers_qwen_backend",
    "parse_observation",
    "refine_candidate",
)


def __getattr__(name: str) -> type[PillowQwenViewRenderer]:
    """Lazily load the optional Pillow-backed production renderer."""
    if name != "PillowQwenViewRenderer":
        raise AttributeError(name)
    from modules.mask_refining.rendering.production_views import (  # noqa: PLC0415
        PillowQwenViewRenderer,
    )

    return PillowQwenViewRenderer
