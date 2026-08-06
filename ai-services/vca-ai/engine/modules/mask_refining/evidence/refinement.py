"""Fail-closed orchestration of visual-only Qwen evidence refinement."""

import math
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Final

from modules.mask_refining.contracts.models import (
    CacheStatus,
    EvidenceContext,
    EvidenceFailure,
    EvidenceFailureCode,
    EvidenceStage,
    QwenEvidenceResult,
    QwenInputView,
    QwenRefinementRequest,
    failure_result,
)
from modules.mask_refining.evidence.observation_parser import parse_observation
from modules.mask_refining.rendering.views import (
    ImageDecodeError,
    QwenViewRenderer,
    renderer_is_independent,
    view_contract_failure,
)
from modules.mask_refining.runtime.backend import (
    QwenBackend,
    QwenBackendRequest,
    backend_is_independent,
    cache_input_views,
)
from modules.shared import PathSafetyError

_PNG_HEADER = b"\x89PNG\r\n\x1a\n"
_SUPPORTED_DEVICES = frozenset({"cpu", "cuda", "mps"})
_ASSET_FAILURE_CODES: Final = {
    "source_path_escape": EvidenceFailureCode.PATH_ESCAPE,
    "source_asset_missing": EvidenceFailureCode.SOURCE_ASSET_MISSING,
    "source_hash_mismatch": EvidenceFailureCode.SOURCE_HASH_MISMATCH,
    "mask_path_escape": EvidenceFailureCode.PATH_ESCAPE,
    "mask_asset_missing": EvidenceFailureCode.MASK_ASSET_MISSING,
    "mask_hash_mismatch": EvidenceFailureCode.MASK_HASH_MISMATCH,
}


def _failure_result(
    request: QwenRefinementRequest,
    views: tuple[QwenInputView, ...],
    failure: EvidenceFailure,
    cache_status: CacheStatus,
) -> QwenEvidenceResult:
    context = EvidenceContext(request.candidate.candidate_id, views, cache_status)
    return failure_result(context, failure)


def _asset_error(
    root: Path, relative_path: str, expected_hash: str, label: str
) -> str | None:
    raw_path = Path(relative_path)
    if raw_path.is_absolute() or ".." in raw_path.parts:
        return f"{label}_path_escape"
    path = (root / raw_path).resolve()
    if not path.is_relative_to(root.resolve()):
        return f"{label}_path_escape"
    if not path.is_file():
        return f"{label}_asset_missing"
    contents = path.read_bytes()
    if sha256(contents).hexdigest() != expected_hash:
        return f"{label}_hash_mismatch"
    return None


def _asset_failure(diagnostic: str, code: EvidenceFailureCode) -> EvidenceFailure:
    return EvidenceFailure(code, diagnostic, EvidenceStage.ASSET_VALIDATION)


def _candidate_asset_failure(
    request: QwenRefinementRequest, root: Path
) -> EvidenceFailure | None:
    source_error = _asset_error(
        root,
        request.source_asset.relative_path,
        request.source_asset.sha256,
        "source",
    )
    if source_error is not None:
        return _asset_failure(source_error, _ASSET_FAILURE_CODES[source_error])
    mask_error = _asset_error(
        root,
        request.candidate.rough_mask.relative_path,
        request.candidate.rough_mask.sha256,
        "mask",
    )
    if mask_error is not None:
        return _asset_failure(mask_error, _ASSET_FAILURE_CODES[mask_error])
    return None


def _candidate_geometry_failure(
    request: QwenRefinementRequest, root: Path
) -> EvidenceFailure | None:
    mask_path = root / request.candidate.rough_mask.relative_path
    if mask_path.read_bytes()[:8] != _PNG_HEADER:
        return _asset_failure(
            "mask_png_signature_invalid",
            EvidenceFailureCode.INVALID_MASK,
        )
    left, top, right, bottom = request.candidate.bbox_xyxy
    values = (left, top, right, bottom)
    if not all(math.isfinite(value) for value in values) or min(values) < 0:
        return _asset_failure(
            "bbox_not_finite_or_negative",
            EvidenceFailureCode.INVALID_BBOX,
        )
    if right <= left or bottom <= top:
        return _asset_failure("bbox_invalid_order", EvidenceFailureCode.INVALID_BBOX)
    if right > request.source_width_px or bottom > request.source_height_px:
        return _asset_failure(
            "bbox_outside_source_image",
            EvidenceFailureCode.INVALID_BBOX,
        )
    return None


def _candidate_error(
    request: QwenRefinementRequest, root: Path
) -> EvidenceFailure | None:
    failure = _candidate_asset_failure(request, root)
    return (
        failure if failure is not None else _candidate_geometry_failure(request, root)
    )


def _pre_backend_failure(
    request: QwenRefinementRequest,
    renderer: QwenViewRenderer,
    backend: QwenBackend,
) -> EvidenceFailure | None:
    if not renderer_is_independent(renderer):
        return EvidenceFailure(
            EvidenceFailureCode.RENDERER_NOT_INDEPENDENT,
            "renderer_provenance_invalid",
            EvidenceStage.VIEW_RENDERING,
        )
    if (
        request.device not in _SUPPORTED_DEVICES
        or backend.device not in _SUPPORTED_DEVICES
    ):
        return EvidenceFailure(
            EvidenceFailureCode.UNSUPPORTED_DEVICE,
            "device_not_supported",
            EvidenceStage.BACKEND_VALIDATION,
        )
    if not backend_is_independent(backend):
        return EvidenceFailure(
            EvidenceFailureCode.BACKEND_NOT_INDEPENDENT,
            "backend_provenance_invalid",
            EvidenceStage.BACKEND_VALIDATION,
        )
    return None


def _render_candidate_views(
    request: QwenRefinementRequest, renderer: QwenViewRenderer
) -> tuple[QwenInputView, ...] | EvidenceFailure:
    try:
        return renderer.render(request.to_view_request())
    except PathSafetyError as error:
        return EvidenceFailure(
            EvidenceFailureCode.PATH_ESCAPE,
            error.reason,
            EvidenceStage.VIEW_RENDERING,
        )
    except ImageDecodeError:
        return EvidenceFailure(
            EvidenceFailureCode.IMAGE_DECODE_FAILED,
            "image_decode_failed",
            EvidenceStage.VIEW_RENDERING,
        )


def refine_candidate(
    request: QwenRefinementRequest,
    renderer: QwenViewRenderer,
    backend: QwenBackend,
) -> QwenEvidenceResult:
    """Produce one non-null visual-only Qwen result for an accepted candidate."""
    root = Path(request.asset_root)
    candidate_failure = _candidate_error(request, root)
    if candidate_failure is not None:
        return _failure_result(request, (), candidate_failure, CacheStatus.MISS)
    rendered = _render_candidate_views(request, renderer)
    if isinstance(rendered, EvidenceFailure):
        return _failure_result(request, (), rendered, CacheStatus.MISS)
    views = rendered
    view_failure = view_contract_failure(views, root)
    if view_failure is not None:
        return _failure_result(
            request,
            views,
            EvidenceFailure(
                view_failure.code,
                view_failure.reason,
                EvidenceStage.VIEW_RENDERING,
            ),
            CacheStatus.MISS,
        )
    backend_failure = _pre_backend_failure(request, renderer, backend)
    if backend_failure is not None:
        return _failure_result(request, views, backend_failure, CacheStatus.MISS)
    response = backend.observe(QwenBackendRequest(views, request.device, root))
    if response.cache_hash != cache_input_views(views):
        failure = EvidenceFailure(
            EvidenceFailureCode.CACHE_HASH_MISMATCH,
            "cache_hash_mismatch",
            EvidenceStage.BACKEND_VALIDATION,
        )
        return _failure_result(
            request,
            views,
            failure,
            response.cache_status,
        )
    parsed = parse_observation(response.raw_output, views)
    return replace(
        parsed,
        candidate_id=request.candidate.candidate_id,
        cache_status=response.cache_status,
    )
