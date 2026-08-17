"""Model-backed preprocessing materialization CLI."""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from PIL import Image

from modules import preprocessing
from modules.preprocessing.assets.manifest_io import (
    detector_input_record,
    write_empty_real_manifest,
    write_preflight_failure,
    write_real_failure,
    write_real_manifest,
)
from modules.preprocessing.assets.materialization import (
    MaterializationContext,
    copy_raw_assets,
    write_detection_assets,
)
from modules.preprocessing.contracts.records import (
    DetectionRun,
    ObjectAssetRecord,
    RealImageRecord,
    RealPreprocessingManifest,
    detector_input_size_for_image,
)
from modules.preprocessing.detection.merge import merge_detection_candidates
from modules.preprocessing.detection.scale_marker import prepare_detector_input
from modules.preprocessing.model_runtime.inventory import (
    inventory_path,
    load_model_inventory,
    validate_model_cache,
)
from modules.preprocessing.model_runtime.lane import owlv2_prompts, supported_real_lane
from modules.preprocessing.model_runtime.options import split_real_options
from modules.preprocessing.model_runtime.runtime import (
    load_runtime_models,
    resolve_runtime_device,
    run_runtime_detection,
)
from modules.shared import ContractValidationError

if TYPE_CHECKING:
    from collections.abc import Sequence


type Clock = Callable[[], datetime]


HELP_OPTIONS: Final = frozenset(("-h", "--help"))
EXECUTION_MANUAL: Final = """Real preprocessing execution manual

Usage:
  uv run python -m modules.preprocessing.pipeline <image ...> [options]
  uv run python -m modules.preprocessing.pipeline --project-name <name> [options]

Canonical 10-image run:
  uv run python -m modules.preprocessing.pipeline test_input/*.jpg \\
    --detector-lane owlv2_sam2 \\
    --device mps \\
    --max-images all \\
    --foreground-white-threshold 215

Project-directory run:
  uv run python -m modules.preprocessing.pipeline --project-name selected2 \\
    --detector-lane owlv2_sam2 \\
    --device mps \\
    --max-images all \\
    --foreground-white-threshold 215

Common options:
  --run-root <path>                  Output directory.
                                     Default: output/preprocessing/<timestamp>
  --project-name <name>              Read inputs from ./<name> when no explicit
                                     images are given and default output to
                                     output/preprocessing/<name>
  --detector-lane owlv2_sam2         Only supported real preprocessing lane
  --device mps                       Real execution requires MPS or CUDA
  --max-images all|<int>             Process all inputs or the first N images
  --foreground-white-threshold <int> Object foreground threshold.
                                     Current working value: 215
  --score-threshold <float>          OWLv2 detection score threshold.
                                     Default: 0.15
  --dry-run                          Validate inputs and write manifests without
                                     model loading

Outputs:
  manifests/real_preprocessing_manifest.json
  assets/objects/<image-id>/<object-id>/mask.png
  assets/objects/<image-id>/<object-id>/bbox_crop.jpg
  assets/objects/<image-id>/<object-id>/alpha_cutout.png
  assets/tiles/<image-id>/<object-id>/tile-###.jpg
"""


def _is_help_request(arguments: Sequence[str]) -> bool:
    return any(argument in HELP_OPTIONS for argument in arguments)


def run(arguments: Sequence[str], *, clock: Clock = datetime.now) -> int:
    """Run real preprocessing materialization for local smoke usage."""
    if _is_help_request(arguments):
        _ = sys.stdout.write(EXECUTION_MANUAL)
        return preprocessing.ExitCode.OK
    passthrough, options = split_real_options(arguments)
    workspace_root = Path.cwd()
    request = preprocessing.parse_run_request(
        passthrough, workspace_root=workspace_root, clock=clock
    )
    receipt = preprocessing.preflight_run(request)
    run_root = request.run_root
    run_root.mkdir(parents=True, exist_ok=True)
    receipt_dir = run_root / "receipts"
    manifest_dir = run_root / "manifests"
    if isinstance(receipt, preprocessing.PreflightFailureReceipt):
        return write_preflight_failure(receipt, receipt_dir)
    manifest = preprocessing.build_input_manifest(receipt)
    copy_raw_assets(manifest)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    _ = (manifest_dir / "input_manifest.json").write_text(manifest.to_json() + "\n")
    if request.dry_run:
        write_empty_real_manifest(manifest_dir, manifest, str(inventory_path(options)))
        return 0
    try:
        lane = supported_real_lane(request)
        device = resolve_runtime_device(request.device)
        model_entries = load_model_inventory(options)
        validate_model_cache(model_entries)
    except ContractValidationError as error:
        return write_real_failure(run_root, error)
    processor, model, predictor = load_runtime_models(model_entries, device)
    prompts = owlv2_prompts()
    object_records: list[ObjectAssetRecord] = []
    sam2_call_count = 0
    image_records: list[RealImageRecord] = []
    selected_images = (
        manifest.images
        if options.max_images is None
        else manifest.images[: options.max_images]
    )
    for image in selected_images:
        scale_result = prepare_detector_input(
            Path(image.original_path),
            run_root,
            str(image.image_id),
            options.scale_removal,
        )
        image_records.append(
            RealImageRecord(
                image_id=str(image.image_id),
                source_image_sha256=image.file_sha256,
                detector_input_sha256=scale_result.detector_input_sha256,
                scale_metadata=scale_result.scale_metadata,
                scale_removal_applied=scale_result.scale_removal_applied,
                scale_removal_mode=scale_result.scale_removal_mode,
                scale_removal_bbox=scale_result.scale_removal_bbox,
                detector_input=detector_input_record(scale_result.detector_input_path),
            )
        )
        with Image.open(scale_result.detector_input_path) as detector_input:
            detector_input_size = detector_input.size
        detector_size = options.detector_input_size or detector_input_size_for_image(
            detector_input_size[0], detector_input_size[1]
        )
        detection_run = DetectionRun(
            processor=processor,
            model=model,
            prompts=prompts,
            threshold=options.score_threshold,
            max_detections=options.max_detections,
            detector_input_size=detector_size,
        )
        raw_detections = run_runtime_detection(
            scale_result.detector_input_path,
            detection_run,
        )
        detections = merge_detection_candidates(
            raw_detections,
            detector_input_size,
            scale_result.scale_removal_bbox,
            options.detection_merge,
        )
        context = MaterializationContext(
            run_root=run_root,
            lane=lane,
            prompt=prompts[0],
            model_entries=model_entries,
            device=device,
            source_image_sha256=image.file_sha256,
            detector_input_sha256=scale_result.detector_input_sha256,
            scale_metadata=scale_result.scale_metadata,
            scale_removal_applied=scale_result.scale_removal_applied,
            foreground_white_threshold=options.foreground_white_threshold,
        )
        sam2_call_count += len(detections)
        object_records.extend(
            write_detection_assets(
                scale_result.detector_input_path,
                str(image.image_id),
                detections,
                predictor,
                context,
            )
        )
    real_manifest = RealPreprocessingManifest(
        schema_version="vca-real-preprocessing-v2",
        model_inventory=str(inventory_path(options)),
        device=device,
        detector_lane_status="real_executed",
        model_invocations=len(selected_images),
        sam2_calls=sam2_call_count,
        manifest_id=manifest.manifest_id,
        processed_image_count=len(selected_images),
        object_count=len(object_records),
        images=tuple(image_records),
        objects=tuple(object_records),
    )
    write_real_manifest(manifest_dir, real_manifest)
    return 0


def main() -> int:
    """Entrypoint for `python -m modules.preprocessing.pipeline`."""
    return run(tuple(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
