"""Lazy SAM2 segmentation and artifact encoding for local rough-mask inference."""

# ruff: noqa: PLC0415
# pyright: reportAny=false, reportArgumentType=false, reportCallIssue=false, reportMissingTypeStubs=false, reportReturnType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import TYPE_CHECKING

from modules.rough_masking.artifacts.materialization import (
    AnomalyMaskOutput,
    MaskOutput,
    RejectedMaskOutput,
)
from modules.rough_masking.artifacts.quality import (
    QUALITY_FILTER_VERSION,
    assess_mask_quality,
)

if TYPE_CHECKING:
    from pathlib import Path

    import numpy as np
    from numpy.typing import NDArray
    from PIL import Image
    from sam2.sam2_image_predictor import SAM2ImagePredictor

    from modules.preprocessing.model_runtime.inventory import ModelInventoryEntry
    from modules.prompt_generating import PromptRecord


@dataclass(frozen=True, slots=True)
class LocalInferenceSettings:
    """Local model locations and target device for one lane inference call."""

    detector_entry: ModelInventoryEntry
    sam2_entry: ModelInventoryEntry
    device: str
    max_mask_area_ratio: float
    object_mask_path: Path
    roi_source_bbox: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class LocalDetection:
    """A detector box retaining its locked prompt provenance."""

    prompt: PromptRecord
    score: float
    bbox_xyxy: tuple[float, float, float, float]


def load_rgb_image(image_path: Path) -> Image.Image:
    """Open an ROI image as RGB only while inference is running."""
    from PIL import Image

    with Image.open(image_path) as source_image:
        return source_image.convert("RGB")


def load_sam2_predictor(settings: LocalInferenceSettings) -> SAM2ImagePredictor:
    """Load SAM2 from the inventory directory without Hugging Face helpers."""
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor

    sam2_dir = settings.sam2_entry.local_dir
    sam2_model = build_sam2(
        "sam2_hiera_l.yaml",
        str(sam2_dir / "sam2_hiera_large.pt"),
        device=settings.device,
    )
    return SAM2ImagePredictor(sam2_model)


def _encode_artifacts(
    image: Image.Image, mask: NDArray[np.bool_]
) -> tuple[bytes, bytes]:
    """Encode a SAM2 boolean mask and a visible RGB overlay."""
    import numpy as np
    from PIL import Image

    mask_image = Image.fromarray(np.where(mask, 255, 0).astype(np.uint8), mode="L")
    mask_buffer = BytesIO()
    mask_image.save(mask_buffer, format="PNG")
    overlay = Image.composite(
        Image.new("RGB", image.size, (255, 0, 0)), image.convert("RGB"), mask_image
    )
    overlay_buffer = BytesIO()
    overlay.save(overlay_buffer, format="JPEG", quality=95)
    return mask_buffer.getvalue(), overlay_buffer.getvalue()


# 전처리 단계의 객체 마스크를 ROI 이미지 크기에 맞춰 자르고 리샘플링한다.
# 이진 마스크를 유지하기 위해 NEAREST 리샘플링을 사용해야 한다(보간 시
# 경계가 흐려져 품질 지표가 왜곡된다).
def _object_foreground_mask(
    settings: LocalInferenceSettings, image_size: tuple[int, int]
) -> NDArray[np.bool_]:
    import numpy as np
    from PIL import Image

    with Image.open(settings.object_mask_path) as mask_image:
        mask = mask_image.convert("L")
        if mask.size != image_size:
            left, top, width, height = settings.roi_source_bbox
            mask = mask.crop((left, top, left + width, top + height))
            mask = mask.resize(image_size, Image.Resampling.NEAREST)
        return np.asarray(mask) > 0


def segment_detections(
    detections: tuple[LocalDetection, ...],
    image: Image.Image,
    settings: LocalInferenceSettings,
) -> tuple[MaskOutput, ...]:
    """Prompt SAM2 with detector XYXY boxes and retain bounded ROI masks."""
    import numpy as np

    predictor = load_sam2_predictor(settings)
    predictor.set_image(np.asarray(image))
    object_foreground = _object_foreground_mask(settings, image.size)
    outputs: list[MaskOutput] = []
    for detection in detections:
        masks, _, _ = predictor.predict(
            box=np.asarray(detection.bbox_xyxy, dtype=np.float32),
            multimask_output=False,
        )
        mask = np.asarray(masks[0]) > 0
        # SAM2는 탐지 박스만으로 프롬프트되므로 박스 내부의 배경까지 마스크에
        # 포함될 수 있다. 품질 지표가 객체 내부 픽셀만 보도록 점수 계산 전에
        # 알려진 객체 실루엣으로 마스크를 클리핑한다.
        mask = mask & object_foreground
        if not np.any(mask):
            continue
        mask_area_ratio = float(np.count_nonzero(mask)) / float(mask.size)
        if mask_area_ratio > settings.max_mask_area_ratio:
            outputs.append(
                RejectedMaskOutput(
                    prompt=detection.prompt,
                    score=detection.score,
                    bbox_xyxy=detection.bbox_xyxy,
                    reject_reason="max_area",
                    quality_filter_version=QUALITY_FILTER_VERSION,
                    quality_score=None,
                    mask_area_ratio=mask_area_ratio,
                    bbox_fill_ratio=None,
                    boundary_pixel_ratio=None,
                    perimeter_coverage_ratio=None,
                    border_touch_count=None,
                    component_count=None,
                    largest_component_ratio=None,
                )
            )
            continue
        quality_decision = assess_mask_quality(mask, object_foreground)
        if not quality_decision.accepted:
            quality = quality_decision.quality
            outputs.append(
                RejectedMaskOutput(
                    prompt=detection.prompt,
                    score=detection.score,
                    bbox_xyxy=detection.bbox_xyxy,
                    reject_reason=quality_decision.reject_reason or "quality_filter",
                    quality_filter_version=quality.filter_version,
                    quality_score=quality.score,
                    mask_area_ratio=quality.area_ratio,
                    bbox_fill_ratio=quality.bbox_fill_ratio,
                    boundary_pixel_ratio=quality.boundary_pixel_ratio,
                    perimeter_coverage_ratio=quality.perimeter_coverage_ratio,
                    border_touch_count=quality.border_touch_count,
                    component_count=quality.component_count,
                    largest_component_ratio=quality.largest_component_ratio,
                )
            )
            continue
        mask_png, overlay_jpeg = _encode_artifacts(image, mask)
        quality = quality_decision.quality
        outputs.append(
            AnomalyMaskOutput(
                prompt=detection.prompt,
                score=detection.score,
                bbox_xyxy=detection.bbox_xyxy,
                mask_png=mask_png,
                overlay_jpeg=overlay_jpeg,
                quality_filter_version=quality.filter_version,
                quality_score=quality.score,
                mask_area_ratio=quality.area_ratio,
                bbox_fill_ratio=quality.bbox_fill_ratio,
                boundary_pixel_ratio=quality.boundary_pixel_ratio,
                perimeter_coverage_ratio=quality.perimeter_coverage_ratio,
                border_touch_count=quality.border_touch_count,
                component_count=quality.component_count,
                largest_component_ratio=quality.largest_component_ratio,
            )
        )
    return tuple(outputs)
