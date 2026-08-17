"""Typed Qwen invocation seam without a heavyweight runtime dependency."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from modules.mask_refining.contracts.models import (
    BackendExecutionKind,
    CacheStatus,
    QwenInputView,
)
from modules.shared import QWEN_BACKEND_DEPENDENCY, QWEN_BACKEND_KIND, QWEN_MODEL_ID


@dataclass(frozen=True, slots=True)
class QwenBackendRequest:
    """The complete typed visual input supplied through the backend seam."""

    input_views: tuple[QwenInputView, ...]
    device: str
    asset_root: Path


@dataclass(frozen=True, slots=True)
class BackendResponse:
    """Raw untrusted model output with cache-accounting evidence."""

    raw_output: str
    cache_status: CacheStatus
    cache_hash: str


class QwenBackend(Protocol):
    """Invokes Qwen over only the two typed visual input assets."""

    @property
    def execution_kind(self) -> BackendExecutionKind:
        """Return backend execution provenance."""
        ...

    @property
    def backend_kind(self) -> str:
        """Return the backend kind identifier."""
        ...

    @property
    def backend_dependency(self) -> str:
        """Return the backend dependency identifier."""
        ...

    @property
    def model_id(self) -> str:
        """Return the model identifier used by the backend."""
        ...

    @property
    def device(self) -> str:
        """Return the backend device identifier."""
        ...

    def observe(self, request: QwenBackendRequest) -> BackendResponse:
        """Return untrusted JSON observation output and cache evidence."""
        ...


def backend_is_independent(backend: QwenBackend) -> bool:
    """Return whether backend provenance permits final production success."""
    return (
        backend.execution_kind is BackendExecutionKind.INDEPENDENT_RAW_IMAGE
        and backend.backend_kind == QWEN_BACKEND_KIND
        and backend.backend_dependency == QWEN_BACKEND_DEPENDENCY
        and backend.model_id == QWEN_MODEL_ID
    )


def cache_input_views(views: tuple[QwenInputView, ...]) -> str:
    """Hash the ordered Qwen input view identities for cache verification."""
    payload = "|".join(f"{view.view_id}:{view.asset_hash}" for view in views)
    return sha256(payload.encode()).hexdigest()
