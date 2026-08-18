"""Restore a rough_masking candidate's view-local bbox/mask to its original photo.

Every accepted rough_masking candidate carries `view_origin_xyxy` (the
rectangle its own view - object crop or tile - occupies in the original
photo) alongside its view-local `bbox_xyxy`/`rough_mask`. This module builds
a `CoordinateTransform` from that field and applies it, without needing
`mask_refining`'s `JoinedRefinementAssets` (an object-crop-only preprocessing
lookup) - any caller that already has a `RawDetectorCandidate` can restore
its own geometry independently. `mask_refining` and the pre-refinement
anomaly-grouping stage both reuse these functions.
"""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING

from modules.preprocessing import BoundingBox, CoordinateTransform
from modules.rough_masking.artifacts.assets import AssetReference
from modules.shared import ContractValidationError

if TYPE_CHECKING:
    from pathlib import Path

    from modules.rough_masking.candidates import RawDetectorCandidate


# candidate.view_origin_xyxy(그 후보를 만든 뷰가 원본 사진에서 차지하는
# 사각형)로부터 즉석에서 CoordinateTransform을 만든다. 이 후보 자신의 뷰(객체일
# 수도, 타일일 수도 있음) 기준이라 join_preprocessing_assets가 주는 객체-레벨
# transform과는 다르다(그건 항상 객체 크롭 기준).
def candidate_view_transform(candidate: RawDetectorCandidate) -> CoordinateTransform:
    """Build a CoordinateTransform from a candidate's own view_origin_xyxy."""
    left, top, right, bottom = candidate.view_origin_xyxy
    return CoordinateTransform(
        BoundingBox(left, top, right - left, bottom - top), left, top
    )


# refine 단계에서 쓰인 뷰 좌표계의 bbox를 CoordinateTransform의
# offset/scale로 원본 이미지 좌표계로 되돌린다. coordinate_transform이
# 없으면 복원할 수 없으므로 예외를 던진다(치명적 오류로 취급).
def restore_original_bbox(
    bbox_xyxy: tuple[float, float, float, float],
    coordinate_transform: CoordinateTransform | None,
    original_image_width_px: int | None,
    original_image_height_px: int | None,
) -> tuple[float, float, float, float]:
    """Restore a view-local bbox to original-image pixel coordinates."""
    if coordinate_transform is None:
        field = "coordinate_transform"
        reason = "required to restore original-image bbox coordinates"
        raise ContractValidationError(field, reason)
    offset_x = coordinate_transform.restore_offset_x
    offset_y = coordinate_transform.restore_offset_y
    scale_x = coordinate_transform.original_to_view_scale_x
    scale_y = coordinate_transform.original_to_view_scale_y
    left, top, right, bottom = bbox_xyxy
    restored = (
        left * scale_x + offset_x,
        top * scale_y + offset_y,
        right * scale_x + offset_x,
        bottom * scale_y + offset_y,
    )
    if original_image_width_px is not None and original_image_height_px is not None:
        _validate_bbox_in_bounds(
            restored, original_image_width_px, original_image_height_px
        )
    return restored


# 복원된 bbox가 원본 이미지 범위를 벗어나지 않는지 확인한다. 좌표
# 변환 정보가 잘못됐을 때 조용히 넘어가지 않고 바로 실패시킨다.
def _validate_bbox_in_bounds(
    bbox_xyxy: tuple[float, float, float, float], width_px: int, height_px: int
) -> None:
    left, top, right, bottom = bbox_xyxy
    if left < 0 or top < 0 or right > width_px or bottom > height_px:
        field = "original_bbox_xyxy"
        reason = "restored bbox must fall within the original image bounds"
        raise ContractValidationError(field, reason)


# candidate.rough_mask가 크롭 로컬(ROI) 좌표계에서 만들어진 마스크를 원본
# 이미지 좌표계로 되돌린다 - restore_original_bbox와 정확히 같은 scale/offset
# 변환을 픽셀 배열에 적용한다(리사이즈 후 offset 위치에 붙여넣기). transform은
# 호출자가 명시적으로 넘긴다 - mask_refining이 정제한 후보는 재탐지 ROI(객체
# 크롭)의 transform을, rough_masking 원본 후보는 candidate_view_transform()이
# 주는 자기 자신의 뷰(객체 또는 타일) transform을 쓴다. mask_root는
# candidate.rough_mask.relative_path가 상대적인 기준 디렉터리로, 호출자가
# 명시적으로 넘겨야 한다(정제된 후보는 그 정제 실행의 lane_output_dir,
# rough_masking 원본 후보는 rough_masking의 rough_root). 반환하는
# AssetReference.relative_path는 절대경로 문자열이다. 원본 이미지 크기를
# 모르면(더 이전 산출물이나 지원 안 하는 이미지 포맷 등) 안전하게 복원할 수
# 없으므로 None을 반환하고, 호출자가 그 실패를 명시적으로 처리하게 둔다.
def restore_original_mask(
    candidate: RawDetectorCandidate,
    transform: CoordinateTransform | None,
    original_image_width_px: int | None,
    original_image_height_px: int | None,
    mask_root: Path,
    output_dir: Path,
) -> AssetReference | None:
    """Restore a view-local mask PNG onto an original-image-sized canvas."""
    if original_image_width_px is None or original_image_height_px is None:
        return None
    if transform is None:
        return None
    # PIL/numpy stay out of this module's top-level imports so importing
    # callers that never actually restore a mask never pulls in image
    # runtimes (mirrors mask_refining.execution.runner's own import policy).
    import numpy as np  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    crop_mask_path = mask_root / candidate.rough_mask.relative_path
    with Image.open(crop_mask_path) as crop_image:
        crop_array = np.asarray(crop_image.convert("L")) > 0
    crop_height, crop_width = crop_array.shape
    target_width = max(1, round(crop_width * transform.original_to_view_scale_x))
    target_height = max(1, round(crop_height * transform.original_to_view_scale_y))
    crop_bytes = np.multiply(crop_array, 255).astype(np.uint8)
    resized = Image.fromarray(crop_bytes).resize(
        (target_width, target_height), Image.Resampling.NEAREST
    )
    canvas = Image.new(
        "L",
        (original_image_width_px, original_image_height_px),
        0,
    )
    canvas.paste(
        resized, (round(transform.restore_offset_x), round(transform.restore_offset_y))
    )
    output_path = output_dir / "restored_masks" / f"{candidate.candidate_id}.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)
    digest = sha256(output_path.read_bytes()).hexdigest()
    return AssetReference(str(output_path), digest, "image/png")
