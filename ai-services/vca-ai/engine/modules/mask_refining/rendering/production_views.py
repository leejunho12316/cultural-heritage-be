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

    # 렌더링된 이미지를 크기 제한을 적용해 PNG로 저장하고, 그 결과를
    # QwenInputView 레코드로 반환한다. render()가 두 뷰 각각에 대해
    # 호출한다.
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


# 후보 bbox 둘레에 PADDING_PX만큼 여백을 더하되 원본 이미지 경계를
# 넘지 않도록 clamp한다. 두 번째 Qwen 뷰(주변 맥락 포함 crop)에 쓰인다.
def _padded_bbox(request: ViewRenderRequest) -> tuple[float, float, float, float]:
    left, top, right, bottom = request.candidate.bbox_xyxy
    return (
        max(0.0, left - PADDING_PX),
        max(0.0, top - PADDING_PX),
        min(float(request.source_width_px), right + PADDING_PX),
        min(float(request.source_height_px), bottom + PADDING_PX),
    )


# 실수 좌표의 crop 상자를 PIL이 요구하는 정수 픽셀 상자로 변환한다.
def _pixel_crop_box(
    crop_xyxy: tuple[float, float, float, float],
) -> tuple[int, int, int, int]:
    left, top, right, bottom = crop_xyxy
    # 반올림이 아니라 바깥쪽으로 확장(floor/ceil)한다: 안쪽으로 줄이면
    # 패딩된 bbox가 포함하려던 mask나 crop 내용이 잘릴 수 있다.
    return math.floor(left), math.floor(top), math.ceil(right), math.ceil(bottom)


# 렌더링된 이미지가 MAX_AREA_PX를 넘으면 비율을 유지한 채 축소한다.
# QwenInputView.__post_init__이 강제하는 면적 상한과 짝을 이루는 로직이다.
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
