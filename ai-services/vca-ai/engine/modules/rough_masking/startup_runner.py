"""Project-level startup runner for rough-mask generation."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

from PIL import Image

from modules.preprocessing import (
    BoundingBox,
    CoordinateTransform,
    ObjectRankingHints,
    ObjectTarget,
    ViewKind,
    ViewPlanningRequest,
    ViewRecord,
)
from modules.preprocessing.views.tiling import (
    target_count,
    tile_boxes,
    tile_size_and_history,
)
from modules.rag.qwen.qwen_bridge_json import parse_json_object
from modules.rough_masking.candidates.normalization import (
    AdapterReceipt,
    execute_adapter,
)
from modules.rough_masking.contracts import (
    AdapterRequest,
    DetectorRunner,
    ImageDimensions,
    SeedRequestPaths,
)
from modules.rough_masking.local_model.runner import (
    LocalModelCachePolicy,
    build_local_model_runner,
)
from modules.rough_masking.routing import (
    RoiRoutingInput,
    RoiViewRequest,
    build_lane_roi_seed_requests,
)
from modules.rough_masking.startup_manifest import (
    StartupManifest,
    load_model_entries,
    load_startup_manifest,
)
from modules.shared import (
    ACTIVE_DETECTOR_LANES,
    BUDGET_THRESHOLDS,
    ContractValidationError,
    DetectorLane,
    ExitCode,
    ImageId,
    update_stage_progress_count,
)

if TYPE_CHECKING:
    from modules.orchestration.stage_paths import StagePathMap
    from modules.preprocessing.contracts.records import ObjectAssetRecord
    from modules.preprocessing.model_runtime.inventory import ModelInventoryEntry

MANIFEST_SCHEMA_VERSION: Final = "vca-real-preprocessing-v2"
DRY_RUN_LANE_STATUS: Final = "dry_run_not_executed"


# 실제 CLI 경로에서만 재현되고 격리 하네스에서는 재현 안 되는 간헐적 정지를
# 잡기 위한 임시 진단 로깅. VCA_DEBUG_TIMING이 설정된 경우에만 오브젝트별/
# ROI별 소요 시간을 stderr로 찍는다 - 기본 동작(프로덕션, 테스트)에는 전혀
# 영향 없음.
def _debug_timing_enabled() -> bool:
    return bool(os.environ.get("VCA_DEBUG_TIMING"))
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

    @property
    def output_root(self) -> Path: ...


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
        budget_error = _check_tile_budget(manifest, request)
        if budget_error is not None:
            print(f"rough_masking: failed: {budget_error}", file=sys.stderr)  # noqa: T201
            return int(ExitCode.INCOMPLETE_OR_FAILURE)
        model_entries = load_model_entries(request.model_cache_root)
        accepted_count = 0
        object_total = len(manifest.objects)
        largest_area = max(
            (_bbox_area(record.bbox_xyxy) for record in manifest.objects),
            default=0.0,
        )
        for object_index, record in enumerate(manifest.objects):
            object_start = time.monotonic() if _debug_timing_enabled() else 0.0
            accepted_count += _run_object(
                record,
                request,
                model_entries,
                runner_factory,
                adapter_executor,
                largest_area,
            )
            if _debug_timing_enabled():
                elapsed = time.monotonic() - object_start
                print(  # noqa: T201
                    f"rough_masking: timing object={record.object_id} "
                    f"elapsed={elapsed:.1f}s index={object_index + 1}/{object_total}",
                    file=sys.stderr,
                )
            update_stage_progress_count(
                request.output_root, object_index + 1, object_total
            )
        return int(
            ExitCode.OK if accepted_count > 0 else ExitCode.INCOMPLETE_OR_FAILURE
        )
    except (ContractValidationError, OSError) as error:
        print(  # noqa: T201
            f"rough_masking: failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return int(ExitCode.INCOMPLETE_OR_FAILURE)


# 전처리에서 accept된 객체 하나에 대해 활성 레인 전부(ACTIVE_DETECTOR_LANES)의
# ROI 요청을 만들고 러너를 실행해 정규화된 candidate 개수를 집계한다. 객체
# 크롭 뷰는 두 레인이 공유하고(같은 사진을 각자 다른 프롬프트로 훑음), 타일
# 뷰는 레인마다 다르다 - OWLv2는 전처리가 이미 잘라둔 record.tiles를,
# GroundingDINO는 이 함수가 그 자리에서 만드는 자기 전용 타일을 쓴다(레인마다
# 목적에 맞는 타일 크기가 다르므로 - _grounded_tile_views 참고). 오브젝트 크롭
# 뷰 하나뿐 아니라 두 레인의 타일 뷰도 각각 자기 자신의 뷰/치수/출력 경로로
# 순회한다.
# run_rough_masking_stage에서 manifest.objects마다 호출된다.
def _run_object(
    record: ObjectAssetRecord,
    request: _RoughMaskingStageRequest,
    model_entries: dict[str, ModelInventoryEntry],
    runner_factory: RunnerFactory,
    adapter_executor: AdapterExecutor,
    largest_area: float,
) -> int:
    preprocessing_root = request.paths.preprocessing.resolve()
    image_path = _contained_file(Path(record.bbox_crop.path), preprocessing_root)
    object_mask_path = _contained_file(Path(record.mask.path), preprocessing_root)
    view = _object_view(record)
    owlv2_tile_views = _tile_views(record, view)
    tile_image_paths = {
        tile_view.tile_view_id: _contained_file(
            Path(tile_asset.path), preprocessing_root
        )
        for tile_view, tile_asset in zip(owlv2_tile_views, record.tiles, strict=True)
    }
    grounded_tile_views = _grounded_tile_views(record, view, largest_area)
    tile_image_paths.update(
        _materialize_grounded_tiles(
            record, grounded_tile_views, image_path, request.paths.rough_masking
        )
    )
    tile_views = owlv2_tile_views + grounded_tile_views
    accepted_count = 0
    for lane in ACTIVE_DETECTOR_LANES:
        route = RoiRoutingInput(
            lane=lane,
            object_request=RoiViewRequest(
                view=view,
                image_dimensions=_image_dimensions(image_path),
                paths=_lane_view_paths(
                    request, record, lane, "object", object_mask_path
                ),
            ),
            tile_requests=tuple(
                RoiViewRequest(
                    view=tile_view,
                    image_dimensions=_image_dimensions(
                        tile_image_paths[tile_view.tile_view_id]
                    ),
                    paths=_lane_view_paths(
                        request,
                        record,
                        lane,
                        tile_view.tile_view_id or "tile",
                        object_mask_path,
                    ),
                )
                for tile_view in tile_views
                if tile_view.lane is lane
            ),
        )
        for adapter_request in build_lane_roi_seed_requests(route):
            request_image_path = (
                tile_image_paths[adapter_request.view.tile_view_id]
                if adapter_request.view.tile_view_id is not None
                else image_path
            )
            roi_start = time.monotonic() if _debug_timing_enabled() else 0.0
            runner = runner_factory(
                lane=adapter_request.lane,
                image_path=request_image_path,
                model_entries=model_entries,
                device=request.device,
                model_cache_policy=LocalModelCachePolicy(
                    root=request.model_cache_root,
                    verify_hashes=request.verify_model_hashes,
                ),
            )
            receipt = adapter_executor(adapter_request, runner)
            accepted_count += len(receipt.candidates)
            if _debug_timing_enabled():
                roi_elapsed = time.monotonic() - roi_start
                view_label = adapter_request.view.tile_view_id or "object"
                print(  # noqa: T201
                    f"rough_masking: timing roi object={record.object_id} "
                    f"lane={adapter_request.lane.value} view={view_label} "
                    f"elapsed={roi_elapsed:.1f}s candidates={len(receipt.candidates)}",
                    file=sys.stderr,
                )
    return accepted_count


# 오브젝트/타일 뷰 하나의 lane별 출력 경로(records.json 등)를 만든다.
# view_segment로 오브젝트 자신("object")과 타일마다(tile_view_id)
# 서로 다른 lane_output_dir을 줘서, 같은 records_json에 여러 뷰의 탐지
# 결과가 덮어써지지 않게 한다. object_mask_path는 이미 _run_object에서
# preprocessing_root 하위인지 한 번 검증됐으므로 여기서 다시 확인하지
# 않는다 - 모든 view_segment에 대해 record.mask.path로 항상 동일하다.
def _lane_view_paths(
    request: _RoughMaskingStageRequest,
    record: ObjectAssetRecord,
    lane: DetectorLane,
    view_segment: str,
    object_mask_path: Path,
) -> SeedRequestPaths:
    lane_output_dir = (
        request.paths.rough_masking
        / lane.value
        / record.object_id
        / lane.value
        / view_segment
    )
    return SeedRequestPaths(
        lane_output_dir=lane_output_dir,
        records_json=lane_output_dir / "records.json",
        object_mask_path=object_mask_path,
    )


def _invalid(field: str, reason: str) -> ContractValidationError:
    return ContractValidationError(field, reason)


# dry-run 매니페스트는 lane 상태가 dry_run이고 호출/객체 수가 모두 0이어야
# 한다. 실제 모델 호출 없이 전처리가 스킵되었는지 확인하는 계약이다.
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


# 실제 실행 매니페스트는 lane 상태가 real_executed이고 객체가 하나 이상
# 있어야 한다.
def _require_real_manifest(manifest: StartupManifest) -> None:
    if manifest.detector_lane_status != REAL_LANE_STATUS:
        field = "preprocessing_manifest"
        reason = "real manifest required"
        raise _invalid(field, reason)
    if manifest.object_count == 0:
        field = "preprocessing_manifest.objects"
        reason = "must not be empty"
        raise _invalid(field, reason)


_BUDGET_REQUEST_FILENAME: Final = "budget_approval_request.json"
_BUDGET_APPROVAL_FILENAME: Final = "budget_approval.json"


# 오브젝트 하나가 최대 수십~수백 개까지 타일로 쪼개질 수 있어(D010 랭킹
# 타일링), 실제 아티팩트에서는 전체 계획 타일 수가 쉽게 수천을 넘을 수
# 있다 - 이 상황에서 아무 안내 없이 그대로 진행하면 탐지 호출이 그만큼
# 폭주한다. RealPreprocessingManifest.to_jsonable()이 이미 계산해서 써주는
# requires_user_budget_approval을 여기서 실제로 확인해, 초과 시 요청
# 아티팩트를 남기고 승인 아티팩트가 일치할 때만 통과시킨다. dry_run_id 등
# RAG 단계까지 아우르는 shared/approvals.py의 전체 승인 스키마는 RAG 설정이
# 아직 없는 이 시점엔 맞지 않아, 타일 예산 전용으로 범위를 좁힌 단순한
# 버전을 쓴다.
def _check_tile_budget(
    manifest: StartupManifest, request: _RoughMaskingStageRequest
) -> str | None:
    """Return a failure message if the tile budget is exceeded and unapproved."""
    if not manifest.requires_user_budget_approval:
        return None
    approval_path = request.paths.rough_masking / _BUDGET_APPROVAL_FILENAME
    if approval_path.is_file() and _tile_budget_approval_matches(
        approval_path, manifest
    ):
        return None
    request.paths.rough_masking.mkdir(parents=True, exist_ok=True)
    request_path = request.paths.rough_masking / _BUDGET_REQUEST_FILENAME
    limit = BUDGET_THRESHOLDS.max_planned_tiles_without_approval
    _ = request_path.write_text(
        json.dumps(
            {
                "object_count": manifest.object_count,
                "tile_budget_limit": limit,
                "tile_count": manifest.tile_count,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return (
        f"planned tile count {manifest.tile_count} exceeds the {limit}-tile budget "
        f"without approval; wrote {request_path}. To proceed, write a "
        f"{_BUDGET_APPROVAL_FILENAME} with matching object_count/tile_count into "
        f"{request.paths.rough_masking} and rerun."
    )


def _tile_budget_approval_matches(
    approval_path: Path, manifest: StartupManifest
) -> bool:
    try:
        approval = parse_json_object(approval_path.read_text(encoding="utf-8"))
    except (OSError, ContractValidationError):
        return False
    return (
        approval.get("tile_count") == manifest.tile_count
        and approval.get("object_count") == manifest.object_count
    )


# 전처리 단계의 bbox_xyxy로부터 객체 크롭 뷰를 합성한다. coordinate_transform은
# 크롭 좌표를 원본 이미지 좌표로 되돌리는 데 쓰이므로, source_bbox 값이
# bbox_xyxy와 어긋나면 하류의 좌표 변환이 모두 틀어진다.
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


# 전처리가 이미 실제로 잘라 저장해둔 record.tiles/tile_bboxes로부터 타일
# 뷰를 만든다. tile_bboxes는 원본 이미지 좌표계(bbox_xyxy와 동일)라서,
# _object_view와 동일한 방식으로 coordinate_transform을 구성하면 각 타일의
# 탐지 좌표가 그대로 원본 이미지 좌표로 복원된다. source_view_id는
# object_view를 가리켜, 이 타일이 어느 오브젝트 크롭에서 파생됐는지
# 남긴다.
def _tile_views(
    record: ObjectAssetRecord, object_view: ViewRecord
) -> tuple[ViewRecord, ...]:
    views: list[ViewRecord] = []
    for index, bbox in enumerate(record.tile_bboxes):
        left, top, right, bottom = bbox
        tile_view_id = f"rough-mask-tile:{record.object_id}:{index:03d}"
        views.append(
            ViewRecord(
                view_id=tile_view_id,
                kind=ViewKind.RANKED_OBJECT_TILE,
                image_id=ImageId(record.image_id),
                object_id=record.object_id,
                tile_view_id=tile_view_id,
                source_view_id=object_view.view_id,
                rag_followup_view_id=None,
                view_reuse_mode=None,
                coordinate_transform=CoordinateTransform(
                    BoundingBox(left, top, right - left, bottom - top),
                    left,
                    top,
                ),
                scale_metadata=record.scale_metadata,
                lane=record.lane,
            )
        )
    return tuple(views)


def _bbox_area(bbox_xyxy: tuple[float, float, float, float]) -> float:
    left, top, right, bottom = bbox_xyxy
    return max(0.0, right - left) * max(0.0, bottom - top)


# GroundingDINO 전용 타일 뷰를 그 자리에서 계산한다. 전처리는 OWLv2 전용이라
# (modules.preprocessing.model_runtime.lane.supported_real_lane) GroundingDINO
# 크기 타일을 미리 잘라두지 않는다 - tiling.py의 레인별 공식
# (_span_values/_largest_target, GroundingDINO는 OWLv2보다 더 크고 적은 타일)을
# 그대로 재사용해 이 함수가 대신 계산한다. 실제 이미지 크롭/저장은
# _materialize_grounded_tiles가 따로 한다.
def _grounded_tile_views(
    record: ObjectAssetRecord, object_view: ViewRecord, largest_area: float
) -> tuple[ViewRecord, ...]:
    left, top, right, bottom = record.bbox_xyxy
    bbox = BoundingBox(left, top, right - left, bottom - top)
    object_target = ObjectTarget(
        bbox=bbox,
        # 랭킹 점수는 안 쓴다(타일 크기 계산에만 필요) - 값 자체는 [0,1] 범위만
        # 만족하면 되는 자리채움이다.
        ranking_hints=ObjectRankingHints(0.0, 0.0, 0.0, 0.0),
    )
    plan_request = ViewPlanningRequest(
        image_id=ImageId(record.image_id),
        image_width_px=max(1, round(bbox.width)),
        image_height_px=max(1, round(bbox.height)),
        objects=(object_target,),
        scale_metadata=record.scale_metadata,
        detector_lanes=(DetectorLane.GROUNDED_SAM2,),
    )
    target = target_count(bbox.area, largest_area, DetectorLane.GROUNDED_SAM2)
    tile_size, _history, _strategy = tile_size_and_history(
        plan_request, object_target, DetectorLane.GROUNDED_SAM2, target
    )
    views: list[ViewRecord] = []
    for index, tile_box in enumerate(tile_boxes(bbox, tile_size)):
        tile_view_id = f"rough-mask-tile:{record.object_id}:grounded_sam2:{index:03d}"
        views.append(
            ViewRecord(
                view_id=tile_view_id,
                kind=ViewKind.RANKED_OBJECT_TILE,
                image_id=ImageId(record.image_id),
                object_id=record.object_id,
                tile_view_id=tile_view_id,
                source_view_id=object_view.view_id,
                rag_followup_view_id=None,
                view_reuse_mode=None,
                coordinate_transform=CoordinateTransform(
                    tile_box,
                    tile_box.left,
                    tile_box.top,
                ),
                scale_metadata=record.scale_metadata,
                lane=DetectorLane.GROUNDED_SAM2,
            )
        )
    return tuple(views)


_GROUNDED_TILE_DIRNAME: Final = "grounded_sam2_tiles"


# _grounded_tile_views가 계산한 타일 bbox(원본 이미지 좌표계)를 이미 로드된
# 객체 크롭 이미지(object_image_path)에서 직접 잘라낸다 - 객체 크롭 자체가
# record.bbox_xyxy 위치에서 뜬 크롭이라, 그 왼쪽/위 오프셋만 빼면 크롭
# 로컬 픽셀 좌표가 되어 원본 사진 파일을 다시 열 필요가 없다. 결과는
# rough_masking 자신의 출력 트리에 저장한다(전처리 산출물 트리는 OWLv2
# 전용으로 그대로 둔다).
def _materialize_grounded_tiles(
    record: ObjectAssetRecord,
    tile_views: tuple[ViewRecord, ...],
    object_image_path: Path,
    rough_masking_root: Path,
) -> dict[str, Path]:
    if not tile_views:
        return {}
    object_left, object_top, _, _ = record.bbox_xyxy
    output_dir = rough_masking_root / _GROUNDED_TILE_DIRNAME / record.object_id
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    with Image.open(object_image_path) as object_image:
        rgb_image = object_image.convert("RGB")
        for index, tile_view in enumerate(tile_views):
            transform = tile_view.coordinate_transform
            assert transform is not None  # noqa: S101 - built by _grounded_tile_views, always set
            source = transform.source_bbox
            crop_box = (
                round(source.left - object_left),
                round(source.top - object_top),
                round(source.left - object_left + source.width),
                round(source.top - object_top + source.height),
            )
            tile_path = output_dir / f"tile-{index:03d}.jpg"
            rgb_image.crop(crop_box).save(tile_path, format="JPEG", quality=92)
            paths[tile_view.tile_view_id or tile_path.stem] = tile_path
    return paths


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
