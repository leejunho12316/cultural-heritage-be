"""Project-level startup runner for rough-mask generation."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

from PIL import Image

from modules.preprocessing import (
    BoundingBox,
    CoordinateTransform,
    ViewKind,
    ViewRecord,
)
from modules.rough_masking.candidates.normalization import (
    AdapterReceipt,
    execute_adapter,
)
from modules.rough_masking.contracts import (
    AdapterRequest,
    DetectorRunner,
    ImageDimensions,
)
from modules.rough_masking.local_model.runner import (
    LocalModelCachePolicy,
    build_local_model_runner,
)
from modules.rough_masking.routing import (
    RoiRoutingInput,
    build_lane_roi_seed_requests,
    seed_paths_from_preprocessing_object,
)
from modules.rough_masking.startup_manifest import (
    StartupManifest,
    load_model_entries,
    load_startup_manifest,
)
from modules.shared import (
    ContractValidationError,
    DetectorLane,
    ExitCode,
    ImageId,
)

if TYPE_CHECKING:
    from modules.orchestration.stage_paths import StagePathMap
    from modules.preprocessing.contracts.records import ObjectAssetRecord
    from modules.preprocessing.model_runtime.inventory import ModelInventoryEntry

MANIFEST_SCHEMA_VERSION: Final = "vca-real-preprocessing-v2"
DRY_RUN_LANE_STATUS: Final = "dry_run_not_executed"
REAL_LANE_STATUS: Final = "real_executed"


class RunnerFactory(Protocol):
    """Build a detector runner for one lane and ROI image."""

    def __call__(
        self,
        *,
        lane: DetectorLane,
        image_path: Path,
        model_entries: dict[str, ModelInventoryEntry],
        device: str,
        model_cache_policy: LocalModelCachePolicy | None = None,
    ) -> DetectorRunner:
        """Return the runner used by execute_adapter."""
        ...


class AdapterExecutor(Protocol):
    """Invoke and normalize one adapter request."""

    def __call__(
        self, request: AdapterRequest, runner: DetectorRunner
    ) -> AdapterReceipt:
        """Return normalized execution evidence for one adapter request."""
        ...


class _RoughMaskingStageRequest(Protocol):
    """Project context needed by the rough-mask startup runner."""

    @property
    def project_name(self) -> str: ...

    @property
    def stage_name(self) -> str: ...

    @property
    def paths(self) -> StagePathMap: ...

    @property
    def device(self) -> str: ...

    @property
    def model_cache_root(self) -> Path: ...

    @property
    def dry_run(self) -> bool: ...

    @property
    def verify_model_hashes(self) -> bool: ...


def run_rough_masking_stage(
    request: _RoughMaskingStageRequest,
    *,
    runner_factory: RunnerFactory = build_local_model_runner,
    adapter_executor: AdapterExecutor = execute_adapter,
) -> int:
    """Run rough masking for every preprocessing object in a startup project."""
    try:
        manifest = load_startup_manifest(request.paths.preprocessing)
        if request.dry_run:
            _require_dry_run_manifest(manifest)
            return int(ExitCode.OK)
        _require_real_manifest(manifest)
        model_entries = load_model_entries(request.model_cache_root)
        accepted_count = 0
        for record in manifest.objects:
            accepted_count += _run_object(
                record, request, model_entries, runner_factory, adapter_executor
            )
        return int(
            ExitCode.OK if accepted_count > 0 else ExitCode.INCOMPLETE_OR_FAILURE
        )
    except (ContractValidationError, OSError):
        return int(ExitCode.INCOMPLETE_OR_FAILURE)


def _run_object(
    record: ObjectAssetRecord,
    request: _RoughMaskingStageRequest,
    model_entries: dict[str, ModelInventoryEntry],
    runner_factory: RunnerFactory,
    adapter_executor: AdapterExecutor,
) -> int:
    preprocessing_root = request.paths.preprocessing.resolve()
    image_path = _contained_file(Path(record.bbox_crop.path), preprocessing_root)
    object_mask_path = _contained_file(Path(record.mask.path), preprocessing_root)
    dimensions = _image_dimensions(image_path)
    view = _object_view(record)
    accepted_count = 0
    for lane in (record.lane,):
        lane_output_dir = (
            request.paths.rough_masking / lane.value / record.object_id / lane.value
        )
        paths = seed_paths_from_preprocessing_object(
            record,
            lane_output_dir,
            lane_output_dir / "records.json",
        )
        if paths.object_mask_path != object_mask_path:
            field = "object_mask_path"
            reason = "preprocessing object mask path escaped root"
            raise _invalid(field, reason)
        route = RoiRoutingInput(lane, view, (), dimensions, paths)
        for adapter_request in build_lane_roi_seed_requests(route):
            runner = runner_factory(
                lane=adapter_request.lane,
                image_path=image_path,
                model_entries=model_entries,
                device=request.device,
                model_cache_policy=LocalModelCachePolicy(
                    root=request.model_cache_root,
                    verify_hashes=request.verify_model_hashes,
                ),
            )
            receipt = adapter_executor(adapter_request, runner)
            accepted_count += len(receipt.candidates)
    return accepted_count


def _invalid(field: str, reason: str) -> ContractValidationError:
    return ContractValidationError(field, reason)


def _require_dry_run_manifest(manifest: StartupManifest) -> None:
    if (
        manifest.detector_lane_status != DRY_RUN_LANE_STATUS
        or manifest.model_invocations != 0
        or manifest.sam2_calls != 0
        or manifest.object_count != 0
    ):
        field = "preprocessing_manifest"
        reason = "dry-run manifest required"
        raise _invalid(field, reason)


def _require_real_manifest(manifest: StartupManifest) -> None:
    if manifest.detector_lane_status != REAL_LANE_STATUS:
        field = "preprocessing_manifest"
        reason = "real manifest required"
        raise _invalid(field, reason)
    if manifest.object_count == 0:
        field = "preprocessing_manifest.objects"
        reason = "must not be empty"
        raise _invalid(field, reason)


def _object_view(record: ObjectAssetRecord) -> ViewRecord:
    left, top, right, bottom = record.bbox_xyxy
    crop_left = float(round(left))
    crop_top = float(round(top))
    crop_right = float(round(right))
    crop_bottom = float(round(bottom))
    return ViewRecord(
        view_id=f"rough-mask-object:{record.object_id}",
        kind=ViewKind.OBJECT_CROP,
        image_id=ImageId(record.image_id),
        object_id=record.object_id,
        tile_view_id=None,
        source_view_id=f"preprocessing-object:{record.object_id}",
        rag_followup_view_id=None,
        view_reuse_mode=None,
        coordinate_transform=CoordinateTransform(
            BoundingBox(
                crop_left,
                crop_top,
                crop_right - crop_left,
                crop_bottom - crop_top,
            ),
            crop_left,
            crop_top,
        ),
        scale_metadata=record.scale_metadata,
    )


def _contained_file(path: Path, root: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        field = "preprocessing_asset"
        reason = "must be contained file"
        raise _invalid(field, reason)
    return resolved


def _image_dimensions(path: Path) -> ImageDimensions:
    with Image.open(path) as image:
        return ImageDimensions(image.width, image.height)
