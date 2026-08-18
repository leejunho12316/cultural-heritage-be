"""Shared fixtures for rough-mask startup runner tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PIL import Image

from modules.orchestration.stage_execution import ProjectStageRequest
from modules.orchestration.stage_paths import stage_paths
from modules.rough_masking import (
    AdapterRequest,
    AssetReference,
    CandidateStatus,
    RawDetectorCandidate,
    SeedThresholds,
)
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


def make_request(
    tmp_path: Path,
    *,
    dry_run: bool = False,
    verify_model_hashes: bool = True,
) -> ProjectStageRequest:
    """Build a project-stage request rooted in the pytest temp directory."""
    return ProjectStageRequest(
        "project-001",
        "rough_masking",
        stage_paths(tmp_path, "project-001"),
        "mps",
        tmp_path / "models",
        dry_run,
        verify_model_hashes,
        tmp_path,
    )


def write_dry_manifest(stage_request: ProjectStageRequest) -> None:
    """Write the model-free preprocessing dry-run manifest."""
    manifest_dir = stage_request.paths.preprocessing / "manifests"
    manifest_dir.mkdir(parents=True)
    _ = (manifest_dir / "real_preprocessing_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "vca-real-preprocessing-v2",
                "detector_lane_status": "dry_run_not_executed",
                "model_invocations": 0,
                "sam2_calls": 0,
                "object_count": 0,
                "objects": [],
                "tile_count": 0,
                "requires_user_budget_approval": False,
            }
        ),
        encoding="utf-8",
    )


@dataclass(frozen=True, slots=True)
class RealManifestPaths:
    """Real preprocessing asset paths written by write_real_manifest_assets."""

    crop_path: Path
    tile_path: Path


def write_real_manifest_assets(
    stage_request: ProjectStageRequest,
    *,
    tile_count: int = 1,
    requires_user_budget_approval: bool = False,
) -> RealManifestPaths:
    """Write one real preprocessing object manifest and its image assets."""
    object_root = (
        stage_request.paths.preprocessing / "assets" / "objects" / "object-001"
    )
    object_root.mkdir(parents=True)
    crop_path = object_root / "bbox_crop.png"
    mask_path = object_root / "mask.png"
    Image.new("RGB", (19, 11), (255, 255, 255)).save(crop_path, format="PNG")
    Image.new("L", (19, 11), 255).save(mask_path, format="PNG")
    tile_path = object_root / "tile-001.jpg"
    Image.new("RGB", (8, 8), (255, 255, 255)).save(tile_path, format="JPEG")
    payload = _object_payload(crop_path, mask_path, tile_path)
    _write_real_manifest(
        stage_request,
        payload,
        tile_count=tile_count,
        requires_user_budget_approval=requires_user_budget_approval,
    )
    return RealManifestPaths(crop_path, tile_path)


def write_model_inventory(stage_request: ProjectStageRequest) -> None:
    """Write the model inventory shape consumed by the startup runner."""
    inventory_dir = stage_request.model_cache_root / "inventory"
    inventory_dir.mkdir(parents=True)
    _ = (inventory_dir / "model_inventory.json").write_text(
        json.dumps(
            {
                "models": [
                    _model("owlv2_sam2.detector", stage_request, "owl"),
                    _model("florence2_sam2.detector", stage_request, "florence"),
                    _model("grounded_sam2.detector", stage_request, "grounded"),
                    _model("sam2.segmenter", stage_request, "sam2"),
                ]
            }
        ),
        encoding="utf-8",
    )


def make_candidate(adapter_request: AdapterRequest) -> RawDetectorCandidate:
    """Build one accepted candidate for the fake adapter receipt."""
    return RawDetectorCandidate(
        CandidateId(f"candidate-{adapter_request.lane.value}"),
        ImageId(adapter_request.view.image_id),
        adapter_request.lane,
        CandidateStatus.ACCEPTED,
        adapter_request.prompts[0].prompt_text,
        0.7,
        (1.0, 1.0, 2.0, 2.0),
        AssetReference("mask.png", "a" * 64, "image/png"),
        AssetReference("overlay.jpg", "b" * 64, "image/jpeg"),
        adapter_request.detector_model_id,
        adapter_request.sam2_model_id,
        SeedThresholds(0.08, None, 2, 0.30),
        PromptMetadata(
            "static-seed-minimal-pack-v1",
            PromptRole.STATIC_SEED,
            RagLane.OWLV2,
            "generated-001",
            (),
        ),
        adapter_request.view.view_id,
        adapter_request.view.object_id,
        adapter_request.view.tile_view_id,
        (),
        _view_origin_xyxy(adapter_request),
        None,
    )


def _view_origin_xyxy(
    adapter_request: AdapterRequest,
) -> tuple[float, float, float, float]:
    transform = adapter_request.view.coordinate_transform
    if transform is None:
        return (0.0, 0.0, float(adapter_request.image_width_px), float(
            adapter_request.image_height_px
        ))
    source_bbox = transform.source_bbox
    return (
        source_bbox.left,
        source_bbox.top,
        source_bbox.left + source_bbox.width,
        source_bbox.top + source_bbox.height,
    )


def _asset(path: Path, media_type: str = "image/png") -> dict[str, str]:
    return {"path": str(path), "sha256": "0" * 64, "media_type": media_type}


def _scale_metadata() -> dict[str, str | bool | list[str] | None]:
    return {
        "scale_marker_detected": False,
        "scale_marker_bbox": None,
        "scale_marker_width_px": None,
        "scale_unit_px": None,
        "scale_unit_source": None,
        "scale_confidence": "unavailable",
        "confidence_reasons": ["fixture"],
        "fallback_reason": "unavailable",
        "scale_unit_label": None,
        "scale_unit_value": None,
        "scale_marker_orientation": None,
        "scale_recognition_method": "manual_or_fixture",
    }


def _object_payload(
    crop_path: Path, mask_path: Path, tile_path: Path
) -> dict[str, object]:
    return {
        "candidate_id": "candidate-001",
        "object_id": "object-001",
        "image_id": "image-001",
        "lane": DetectorLane.OWLV2_SAM2.value,
        "accepted": True,
        "diagnostics": [],
        "bbox_xyxy": [1.0, 2.0, 20.0, 13.0],
        "score": 0.7,
        "sam2_score": 0.9,
        "prompt_pack_id": "static-seed-minimal-pack-v1",
        "prompt_role": "static_seed",
        "prompt_text": "surface crack",
        "generated_prompt_id": "prompt-001",
        "source_terms": [],
        "detector_model_id": "detector",
        "detector_model_revision": "revision",
        "sam2_model_id": "sam2",
        "sam2_model_revision": "revision",
        "device": "mps",
        "source_image_sha256": "1" * 64,
        "detector_input_sha256": "2" * 64,
        "scale_metadata": _scale_metadata(),
        "scale_removal_applied": False,
        "mask": _asset(mask_path),
        "bbox_crop": _asset(crop_path),
        "alpha_cutout": _asset(mask_path),
        "detection_overlay": _asset(crop_path, "image/jpeg"),
        "tile": _asset(tile_path, "image/jpeg"),
        "tiles": [_asset(tile_path, "image/jpeg")],
        "tile_bboxes": [[1.0, 2.0, 20.0, 13.0]],
    }


def _write_real_manifest(
    stage_request: ProjectStageRequest,
    payload: dict[str, object],
    *,
    tile_count: int = 1,
    requires_user_budget_approval: bool = False,
) -> None:
    manifest_dir = stage_request.paths.preprocessing / "manifests"
    manifest_dir.mkdir(parents=True)
    _ = (manifest_dir / "real_preprocessing_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "vca-real-preprocessing-v2",
                "detector_lane_status": "real_executed",
                "model_invocations": 1,
                "sam2_calls": 1,
                "object_count": 1,
                "objects": [payload],
                "tile_count": tile_count,
                "requires_user_budget_approval": requires_user_budget_approval,
            }
        ),
        encoding="utf-8",
    )


def _model(
    key: str, stage_request: ProjectStageRequest, directory_name: str
) -> dict[str, str]:
    return {
        "key": key,
        "repo_id": "repo",
        "revision": "revision",
        "local_dir": str(stage_request.model_cache_root / directory_name),
    }
