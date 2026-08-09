from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules.preprocessing.detection.merge import DetectionMergeStrategy
from modules.preprocessing.model_runtime.inventory import (
    inventory_path,
    model_paths,
)
from modules.preprocessing.model_runtime.options import split_real_options
from modules.shared import ContractValidationError


def _write_inventory(root: Path) -> None:
    inventory_dir = root / "inventory"
    inventory_dir.mkdir(parents=True)
    _ = (inventory_dir / "model_inventory.json").write_text(
        json.dumps(
            {
                "schema_version": "vca-shared-model-inventory-v1",
                "models": [
                    {
                        "key": "owlv2_sam2.detector",
                        "repo_id": "google/owlv2-base-patch16-ensemble",
                        "revision": "main",
                        "local_dir": "models/hf/google/owlv2-base-patch16-ensemble",
                    },
                    {
                        "key": "sam2.segmenter",
                        "repo_id": "facebook/sam2-hiera-large",
                        "revision": "main",
                        "local_dir": "models/hf/facebook/sam2-hiera-large",
                    },
                ],
            }
        )
    )


def test_real_options_split_model_flags_from_preprocessing_cli() -> None:
    # Given: real-model flags mixed with the existing preprocessing CLI contract.
    arguments = (
        "inputs/artifact.jpg",
        "--run-root",
        "runs/current",
        "--model-cache-root",
        "local-models",
        "--max-images",
        "3",
        "--max-detections",
        "4",
        "--detector-input-size",
        "1152",
        "--foreground-white-threshold",
        "235",
        "--score-threshold",
        "0.25",
        "--merge-strategy",
        "union",
        "--merge-edge-gap",
        "4",
        "--merge-min-orthogonal-overlap-ratio",
        "0.5",
        "--scale-marker-coverage-ratio",
        "0.6",
        "--nms-iou-threshold",
        "0.4",
        "--nms-containment-threshold",
        "0.85",
        "--scale-removal-padding-ratio",
        "0.5",
    )

    # When: real-model options are split before preprocessing request parsing.
    passthrough, options = split_real_options(arguments)

    # Then: only existing preprocessing arguments pass through unchanged.
    assert passthrough == ("inputs/artifact.jpg", "--run-root", "runs/current")
    assert options.model_cache_root == Path("local-models")
    assert options.max_images == 3
    assert options.max_detections == 4
    assert options.detector_input_size == 1152
    assert options.foreground_white_threshold == 235
    assert options.score_threshold == 0.25
    assert options.detection_merge.strategy == DetectionMergeStrategy.UNION
    assert options.detection_merge.edge_adjacency_gap == 4.0
    assert options.detection_merge.min_orthogonal_overlap_ratio == 0.5
    assert options.detection_merge.scale_marker_coverage_ratio == 0.6
    assert options.detection_merge.nms_iou_threshold == 0.4
    assert options.detection_merge.nms_containment_threshold == 0.85
    assert options.scale_removal.padding_ratio == 0.5


def test_real_options_default_to_unlimited_detections() -> None:
    # Given: real preprocessing arguments without an explicit detection cap.
    arguments = ("inputs/artifact.jpg", "--run-root", "runs/current")

    # When: real-model options are split before preprocessing request parsing.
    _, options = split_real_options(arguments)

    # Then: OWLv2 detections are unlimited unless the caller opts into a cap,
    # and every uploaded image is processed by default (not just the first) -
    # multi-image artifacts must not be silently truncated to one image.
    assert options.max_images is None
    assert options.max_detections is None
    assert options.detector_input_size is None
    assert options.foreground_white_threshold == 215
    assert options.score_threshold == 0.15
    assert options.detection_merge.strategy == DetectionMergeStrategy.UNION
    assert options.detection_merge.scale_marker_coverage_ratio == 0.8
    assert options.detection_merge.nms_iou_threshold == 0.1
    assert options.detection_merge.nms_containment_threshold == 0.99
    assert options.scale_removal.padding_ratio == 0.35


def test_real_options_accept_all_max_images() -> None:
    # Given: real preprocessing arguments that request every input image.
    arguments = ("inputs/artifact.jpg", "--max-images", "all")

    # When: real-model options are split before preprocessing request parsing.
    passthrough, options = split_real_options(arguments)

    # Then: max_images is unbounded while the source argument passes through.
    assert passthrough == ("inputs/artifact.jpg",)
    assert options.max_images is None


def test_model_inventory_loads_required_local_paths(tmp_path: Path) -> None:
    # Given: a minimal shared model inventory under a selected cache root.
    inventory_root = tmp_path / "models"
    _write_inventory(inventory_root)
    _, options = split_real_options(("--model-cache-root", str(inventory_root)))

    # When: preprocessing loads the local model path map.
    paths = model_paths(options)

    # Then: each key resolves to the declared local cache path.
    assert inventory_path(options) == (
        inventory_root / "inventory" / "model_inventory.json"
    )
    assert paths["owlv2_sam2.detector"] == (
        inventory_root / "hf" / "google" / "owlv2-base-patch16-ensemble"
    )
    assert paths["sam2.segmenter"] == (
        inventory_root / "hf" / "facebook" / "sam2-hiera-large"
    )


def test_model_inventory_rejects_missing_required_key(tmp_path: Path) -> None:
    # Given: an inventory missing required active model entries.
    inventory_root = tmp_path / "models"
    inventory_dir = inventory_root / "inventory"
    inventory_dir.mkdir(parents=True)
    _ = (inventory_dir / "model_inventory.json").write_text(
        json.dumps(
            {
                "models": [
                    {
                        "key": "owlv2_sam2.detector",
                        "repo_id": "google/owlv2-base-patch16-ensemble",
                        "revision": "main",
                        "local_dir": "models/hf/google/owlv2-base-patch16-ensemble",
                    }
                ]
            }
        )
    )
    _, options = split_real_options(("--model-cache-root", str(inventory_root)))

    # When: real preprocessing validates the model inventory boundary.
    with pytest.raises(ContractValidationError) as error:
        _ = model_paths(options)

    # Then: missing keys are reported as a structured contract failure.
    assert error.value.field == "model_inventory.models"
    assert "sam2.segmenter" in error.value.reason
