"""Renderer seam and validation for Qwen input image assets."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from modules.mask_refining.contracts.models import (
    PADDING_PX,
    EvidenceFailureCode,
    QwenInputView,
    QwenViewKind,
    RendererExecutionKind,
    ViewRenderRequest,
)
from modules.shared import (
    CANDIDATE_METADATA_BACKEND_ID,
    QWEN_BACKEND_KIND,
    ensure_asset_path,
)

_REQUIRED_VIEW_COUNT = 2
_PNG_HEADER = b"\x89PNG\r\n\x1a\n"


class ImageDecodeError(Exception):
    """Signal an expected raw-image decoding failure at the renderer boundary."""


class QwenViewRenderer(Protocol):
    """Creates the two visual assets supplied to a Qwen invocation."""

    @property
    def execution_kind(self) -> RendererExecutionKind:
        """Return renderer execution provenance."""
        ...

    @property
    def renderer_kind(self) -> str:
        """Return the renderer kind identifier."""
        ...

    @property
    def renderer_dependency(self) -> str:
        """Return the renderer dependency identifier."""
        ...

    def render(self, request: ViewRenderRequest) -> tuple[QwenInputView, ...]:
        """Render and return both required Qwen input views."""
        ...


def renderer_is_independent(renderer: QwenViewRenderer) -> bool:
    """Return whether renderer provenance permits a final production claim."""
    return (
        renderer.execution_kind is RendererExecutionKind.INDEPENDENT_RAW_IMAGE
        and renderer.renderer_kind == QWEN_BACKEND_KIND
        and renderer.renderer_dependency == CANDIDATE_METADATA_BACKEND_ID
    )


@dataclass(frozen=True, slots=True)
class FileQwenViewRenderer:
    """Materialize deterministic placeholder Qwen view assets on disk."""

    asset_root: Path
    renderer_kind: str = QWEN_BACKEND_KIND
    renderer_dependency: str = CANDIDATE_METADATA_BACKEND_ID

    @property
    def execution_kind(self) -> RendererExecutionKind:
        """Return test provenance because this renderer does not decode raw images."""
        return RendererExecutionKind.TEST_FAKE

    def render(self, request: ViewRenderRequest) -> tuple[QwenInputView, ...]:
        """Render both required Qwen view assets with clipped crop metadata."""
        left, top, right, bottom = _padded_bbox(request)
        return (
            self._write_view(
                request,
                QwenViewKind.MASKED_TARGET_CROP,
                1,
                (left, top, right, bottom),
            ),
            self._write_view(
                request,
                QwenViewKind.BOUNDED_PADDED_CANDIDATE_CROP,
                2,
                (left, top, right, bottom),
            ),
        )

    def _write_view(
        self,
        request: ViewRenderRequest,
        kind: QwenViewKind,
        call_order: int,
        crop_xyxy: tuple[float, float, float, float],
    ) -> QwenInputView:
        view_id = f"{request.candidate.candidate_id}:{kind.value}"
        relative_path = f"qwen_views/{view_id}.png"
        payload = (
            f"{view_id}|{request.source_asset.sha256}|"
            f"{request.candidate.rough_mask.sha256}|{crop_xyxy}"
        ).encode()
        contents = _PNG_HEADER + payload
        path = ensure_asset_path(self.asset_root, relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(contents)
        return QwenInputView(
            view_id=view_id,
            kind=kind,
            asset_hash=sha256(contents).hexdigest(),
            media_type="image/png",
            relative_path=relative_path,
            call_order=call_order,
            crop_xyxy=crop_xyxy,
        )


def _padded_bbox(request: ViewRenderRequest) -> tuple[float, float, float, float]:
    left, top, right, bottom = request.candidate.bbox_xyxy
    return (
        max(0.0, left - PADDING_PX),
        max(0.0, top - PADDING_PX),
        min(float(request.source_width_px), right + PADDING_PX),
        min(float(request.source_height_px), bottom + PADDING_PX),
    )


@dataclass(frozen=True, slots=True)
class _ViewContractFailure:
    code: EvidenceFailureCode
    reason: str


def _view_asset_failure(
    view: QwenInputView, asset_root: Path
) -> _ViewContractFailure | None:
    raw_path = Path(view.relative_path)
    path = (asset_root / raw_path).resolve()
    if (
        raw_path.is_absolute()
        or ".." in raw_path.parts
        or not path.is_relative_to(asset_root.resolve())
    ):
        return _ViewContractFailure(EvidenceFailureCode.PATH_ESCAPE, "view_path_escape")
    if not path.is_file():
        return _ViewContractFailure(
            EvidenceFailureCode.VIEW_ASSET_MISSING,
            "view_asset_missing",
        )
    contents = path.read_bytes()
    if sha256(contents).hexdigest() != view.asset_hash:
        return _ViewContractFailure(
            EvidenceFailureCode.VIEW_HASH_MISMATCH,
            "view_hash_mismatch",
        )
    if path.suffix.lower() != ".png" or view.media_type != "image/png":
        return _ViewContractFailure(
            EvidenceFailureCode.VIEW_MEDIA_TYPE_INVALID,
            "view_media_type_invalid",
        )
    if contents[:8] != _PNG_HEADER:
        return _ViewContractFailure(
            EvidenceFailureCode.VIEW_PNG_INVALID,
            "view_png_invalid",
        )
    return None


def view_contract_failure(
    views: tuple[QwenInputView, ...], asset_root: Path
) -> _ViewContractFailure | None:
    """Return the first fail-closed view asset or ordering violation."""
    if len(views) != _REQUIRED_VIEW_COUNT:
        return _ViewContractFailure(
            EvidenceFailureCode.VIEW_CONTRACT_INVALID,
            "expected_exactly_two_views",
        )
    expected_kinds = (
        QwenViewKind.MASKED_TARGET_CROP,
        QwenViewKind.BOUNDED_PADDED_CANDIDATE_CROP,
    )
    if tuple(view.kind for view in views) != expected_kinds:
        return _ViewContractFailure(
            EvidenceFailureCode.VIEW_CONTRACT_INVALID,
            "view_kind_order_invalid",
        )
    if tuple(view.call_order for view in views) != (1, 2):
        return _ViewContractFailure(
            EvidenceFailureCode.VIEW_CONTRACT_INVALID,
            "view_call_order_invalid",
        )
    for view in views:
        failure = _view_asset_failure(view, asset_root)
        if failure is not None:
            return failure
    return None
