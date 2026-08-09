"""Asset materialization for model-backed preprocessing outputs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from shutil import copy2
from typing import TYPE_CHECKING

import numpy as np
import torch
from PIL import Image, ImageColor, ImageDraw

from modules.preprocessing.assets.component_planning import component_plans
from modules.preprocessing.assets.materialized_masks import component_mask_image
from modules.preprocessing.assets.tile_materialization import (
    TileGenerationInput,
    TilePlanningInput,
    materialize_object_tiles,
    object_target,
    tile_request,
)
from modules.preprocessing.contracts.records import (
    DetectionBox,
    MaterializedAssetRecord,
    ObjectAssetRecord,
)
from modules.shared import ContractValidationError

if TYPE_CHECKING:
    from modules.preprocessing.contracts.views import ScaleMetadata
    from modules.preprocessing.model_runtime.inventory import ModelInventoryEntry
    from modules.preprocessing.model_runtime.models import SamPredictor
    from modules.preprocessing.preflight.manifest import InputManifest
    from modules.prompt_generating import PromptRecord
    from modules.shared import DetectorLane


@dataclass(frozen=True, slots=True)
class MaterializationContext:
    """Shared metadata for one real preprocessing materialization pass."""

    run_root: Path
    lane: DetectorLane
    prompt: PromptRecord
    model_entries: dict[str, ModelInventoryEntry]
    device: str
    source_image_sha256: str
    detector_input_sha256: str
    scale_metadata: ScaleMetadata
    scale_removal_applied: bool
    foreground_white_threshold: int


def copy_raw_assets(manifest: InputManifest) -> None:
    """Copy validated source images into the run asset directory."""
    for image in manifest.images:
        target = Path(image.run_root_asset_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = copy2(image.original_path, target)


# 한 객체의 mask/bbox_crop/alpha_cutout/detection_overlay 산출물 경로를
# 만들고 상위 디렉터리를 준비한다. write_detection_assets에서 객체마다 호출된다.
def _asset_paths(run_root: Path, image_id: str, object_id: str) -> dict[str, Path]:
    object_root = run_root / "assets" / "objects" / image_id / object_id
    tile_root = run_root / "assets" / "tiles" / image_id / object_id
    object_root.mkdir(parents=True, exist_ok=True)
    tile_root.mkdir(parents=True, exist_ok=True)
    return {
        "mask": object_root / "mask.png",
        "bbox_crop": object_root / "bbox_crop.jpg",
        "alpha_cutout": object_root / "alpha_cutout.png",
        "detection_overlay": object_root / "detection_overlay.jpg",
    }


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# 산출물 경로가 run_root 밖으로 벗어나지 않았는지 검증하고 sha256 무결성
# 레코드를 만든다. write_detection_assets에서 각 산출물을 저장한 뒤 호출된다.
def _asset_record(
    run_root: Path, path: Path, media_type: str
) -> MaterializedAssetRecord:
    resolved = path.resolve()
    root = run_root.resolve()
    if not resolved.is_relative_to(root):
        field = "asset_path"
        reason = "outside run root"
        raise ContractValidationError(field, reason)
    return MaterializedAssetRecord(
        path=str(resolved),
        sha256=_file_sha256(resolved),
        media_type=media_type,
    )


# 검출 결과 식별 필드(bbox, image_id, lane, prompt_id, score, 원본 이미지
# 해시)를 정규화해 하나의 candidate_id 해시로 만든다. 이 값은
# ObjectAssetRecord.candidate_id로만 쓰이며, 파이프라인 전체에서 통용되는
# candidate_id(rough_masking이 별도로 재계산)와는 다른 값이다.
# write_detection_assets에서 검출된 객체마다 호출된다.
def _candidate_id(
    context: MaterializationContext, image_id: str, detection: DetectionBox
) -> str:
    identity = json.dumps(
        {
            "bbox_xyxy": (detection.x0, detection.y0, detection.x1, detection.y1),
            "image_id": image_id,
            "lane": context.lane,
            "prompt_id": context.prompt.metadata.generated_prompt_id,
            "score": detection.score,
            "source_image_sha256": context.source_image_sha256,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(identity.encode()).hexdigest()


def write_detection_assets(
    image_path: Path,
    image_id: str,
    detections: tuple[DetectionBox, ...],
    predictor: SamPredictor,
    context: MaterializationContext,
) -> tuple[ObjectAssetRecord, ...]:
    """Write masks, bbox crops, alpha cutouts, QA overlays, and first tiles."""
    image = Image.open(image_path).convert("RGB")
    working = image.copy()
    working.thumbnail((1024, 1024))
    scale_x = working.width / image.width
    scale_y = working.height / image.height
    predictor.set_image(np.array(working))
    records: list[ObjectAssetRecord] = []
    plans = component_plans(image, detections, context.foreground_white_threshold)
    largest_area = max(
        (object_target(plan.detection).bbox.area for plan in plans), default=0.0
    )
    object_index = 1
    for plan in plans:
        component_detection = plan.detection
        object_id = f"{image_id}-object-{object_index:02d}"
        paths = _asset_paths(context.run_root, image_id, object_id)
        object_index += 1
        crop_box = (
            round(component_detection.x0),
            round(component_detection.y0),
            round(component_detection.x1),
            round(component_detection.y1),
        )
        box = np.array(
            [
                component_detection.x0 * scale_x,
                component_detection.y0 * scale_y,
                component_detection.x1 * scale_x,
                component_detection.y1 * scale_y,
            ]
        )
        with torch.inference_mode():
            _, scores, _ = predictor.predict(box=box, multimask_output=False)
        mask = component_mask_image(plan.component, image.size, plan.detection_crop_box)
        crop = image.crop(crop_box)
        crop_mask = mask.crop(crop_box)
        rgba = crop.convert("RGBA")
        rgba.putalpha(crop_mask)
        overlay = image.copy()
        draw = ImageDraw.Draw(overlay, "RGBA")
        draw.rectangle(crop_box, outline=ImageColor.getrgb("red"), width=8)
        draw.rectangle(crop_box, fill=(255, 0, 0, 48))
        mask.save(paths["mask"])
        crop.save(paths["bbox_crop"])
        rgba.save(paths["alpha_cutout"])
        overlay.save(paths["detection_overlay"])
        target = object_target(component_detection)
        request = tile_request(
            TilePlanningInput(
                image=image,
                image_id=image_id,
                lane=context.lane,
                scale_metadata=context.scale_metadata,
                target=target,
            )
        )
        tile_paths = materialize_object_tiles(
            TileGenerationInput(
                image=image,
                image_id=image_id,
                object_id=object_id,
                run_root=context.run_root,
                largest_area=largest_area,
                request=request,
            )
        )
        tile_records = tuple(
            _asset_record(context.run_root, tile_path, "image/jpeg")
            for tile_path in tile_paths
        )
        records.append(
            ObjectAssetRecord(
                candidate_id=_candidate_id(context, image_id, component_detection),
                object_id=object_id,
                image_id=image_id,
                lane=context.lane,
                accepted=True,
                diagnostics=(),
                bbox_xyxy=(
                    component_detection.x0,
                    component_detection.y0,
                    component_detection.x1,
                    component_detection.y1,
                ),
                score=component_detection.score,
                sam2_score=scores.item(0),
                prompt_pack_id=context.prompt.metadata.prompt_pack_id,
                prompt_role=context.prompt.metadata.prompt_role.value,
                prompt_text=component_detection.prompt_text,
                generated_prompt_id=component_detection.generated_prompt_id,
                source_terms=context.prompt.metadata.source_terms,
                detector_model_id=context.model_entries["owlv2_sam2.detector"].repo_id,
                detector_model_revision=context.model_entries[
                    "owlv2_sam2.detector"
                ].revision,
                sam2_model_id=context.model_entries["sam2.segmenter"].repo_id,
                sam2_model_revision=context.model_entries["sam2.segmenter"].revision,
                device=context.device,
                source_image_sha256=context.source_image_sha256,
                detector_input_sha256=context.detector_input_sha256,
                scale_metadata=context.scale_metadata,
                scale_removal_applied=context.scale_removal_applied,
                mask=_asset_record(context.run_root, paths["mask"], "image/png"),
                bbox_crop=_asset_record(
                    context.run_root, paths["bbox_crop"], "image/jpeg"
                ),
                alpha_cutout=_asset_record(
                    context.run_root, paths["alpha_cutout"], "image/png"
                ),
                detection_overlay=_asset_record(
                    context.run_root, paths["detection_overlay"], "image/jpeg"
                ),
                tile=tile_records[0],
                tiles=tile_records,
            )
        )
    return tuple(records)
