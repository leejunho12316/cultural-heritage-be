"""Pillow renderer for independent, budgeted Qwen PNG input views."""

from __future__ import annotations

import math
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from typing import TYPE_CHECKING

from PIL import Image

from modules.mask_refining.contracts.models import (
    MAX_AREA_PX,
    PADDING_PX,
    QwenInputView,
    QwenViewKind,
    RendererExecutionKind,
    ViewRenderRequest,
)
from modules.mask_refining.rendering.views import ImageDecodeError
from modules.shared import (
    CANDIDATE_METADATA_BACKEND_ID,
    QWEN_BACKEND_KIND,
    ensure_asset_path,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class PillowQwenViewRenderer:
    """Render source-and-mask evidence as bounded deterministic PNG assets."""

    asset_root: Path
    renderer_kind: str = QWEN_BACKEND_KIND
    renderer_dependency: str = CANDIDATE_METADATA_BACKEND_ID

    @property
    def execution_kind(self) -> RendererExecutionKind:
        """Return production provenance for decoded raw-image rendering."""
        return RendererExecutionKind.INDEPENDENT_RAW_IMAGE

    def render(self, request: ViewRenderRequest) -> tuple[QwenInputView, ...]:
        """Write the masked target and padded-context PNG views."""
        try:
            source = _open_rgb(
                ensure_asset_path(self.asset_root, request.source_asset.relative_path)
            )
            mask = _open_mask(
                ensure_asset_path(
                    self.asset_root,
                    request.candidate.rough_mask.relative_path,
                )
            )
        except (Image.DecompressionBombError, OSError, SyntaxError) as error:
            raise ImageDecodeError from error
        crop_xyxy = _padded_bbox(request)
        crop_box = _pixel_crop_box(crop_xyxy)
        source_crop = source.crop(crop_box)
        mask_crop = mask.crop(crop_box)
        masked_target = Image.new("RGB", source_crop.size)
        masked_target.paste(source_crop, mask=mask_crop)
        return (
            self._write_view(
                request,
                QwenViewKind.MASKED_TARGET_CROP,
                1,
                crop_xyxy,
                masked_target,
            ),
            self._write_view(
                request,
                QwenViewKind.BOUNDED_PADDED_CANDIDATE_CROP,
                2,
                crop_xyxy,
                source_crop,
            ),
        )

    def _write_view(
        self,
        request: ViewRenderRequest,
        kind: QwenViewKind,
        call_order: int,
        crop_xyxy: tuple[float, float, float, float],
        image: Image.Image,
    ) -> QwenInputView:
        rendered = _bounded_image(image)
        contents = _png_bytes(rendered)
        view_id = f"{request.candidate.candidate_id}:{kind.value}"
        relative_path = f"qwen_views/{view_id}.png"
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
            rendered_width_px=rendered.width,
            rendered_height_px=rendered.height,
        )


def _open_rgb(path: Path) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("RGB")


def _open_mask(path: Path) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("L")


def _padded_bbox(request: ViewRenderRequest) -> tuple[float, float, float, float]:
    left, top, right, bottom = request.candidate.bbox_xyxy
    return (
        max(0.0, left - PADDING_PX),
        max(0.0, top - PADDING_PX),
        min(float(request.source_width_px), right + PADDING_PX),
        min(float(request.source_height_px), bottom + PADDING_PX),
    )


def _pixel_crop_box(
    crop_xyxy: tuple[float, float, float, float],
) -> tuple[int, int, int, int]:
    left, top, right, bottom = crop_xyxy
    return math.floor(left), math.floor(top), math.ceil(right), math.ceil(bottom)


def _bounded_image(image: Image.Image) -> Image.Image:
    area = image.width * image.height
    if area <= MAX_AREA_PX:
        return image
    scale = math.sqrt(MAX_AREA_PX / area)
    width = max(1, math.floor(image.width * scale))
    height = max(1, math.floor(image.height * scale))
    return image.resize((width, height), Image.Resampling.LANCZOS)


def _png_bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()
