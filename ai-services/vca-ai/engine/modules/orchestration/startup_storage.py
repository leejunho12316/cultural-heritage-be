"""Optional RDB startup storage orchestration."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Literal, Protocol

from modules.orchestration.receipts import STAGE_NAMES, StartupStatus
from modules.rag.qwen.qwen_bridge_json import parse_json_object
from modules.shared import ContractValidationError
from modules.storage import (
    RdbStorageCliArguments,
    RdbStorageConfig,
    RdbStorageWriter,
    SqlAlchemyRdbStorageWriter,
    StartupStorageSnapshot,
    StoragePersistenceError,
    build_storage_write_request,
    rdb_storage_config,
)

if TYPE_CHECKING:
    from pathlib import Path

    from modules.orchestration.stage_paths import StagePathMap

type StorageMode = Literal["filesystem", "rdb"]
type StartupStorageConfig = RdbStorageConfig | None
type StorageWriterFactory = Callable[[RdbStorageConfig], RdbStorageWriter]

__all__ = (
    "StartupStorageConfig",
    "StorageMode",
    "StoragePersistenceError",
    "StorageWriterFactory",
    "persist_startup_storage",
    "startup_storage_config",
)


class _StartupStorageRequest(Protocol):
    """Startup request fields required by optional storage persistence."""

    @property
    def project_name(self) -> str: ...

    @property
    def input_image_folder(self) -> Path: ...

    @property
    def output_root(self) -> Path: ...

    @property
    def stage_paths(self) -> StagePathMap: ...

    @property
    def image_paths(self) -> tuple[Path, ...]: ...

    @property
    def device(self) -> str: ...

    @property
    def dry_run(self) -> bool: ...

    @property
    def storage_config(self) -> StartupStorageConfig: ...


class _StartupStorageResult(Protocol):
    """Stage execution result fields required by optional storage persistence."""

    @property
    def status(self) -> StartupStatus: ...

    @property
    def failed_stage(self) -> str | None: ...


def startup_storage_config(
    storage_mode: StorageMode,
    rdb_arguments: RdbStorageCliArguments,
    environment: Mapping[str, str] = os.environ,
) -> StartupStorageConfig:
    """Parse the optional startup storage selection from CLI-shaped values."""
    match storage_mode:
        case "filesystem":
            return None
        case "rdb":
            return rdb_storage_config(rdb_arguments, environment)


def persist_startup_storage(
    request: _StartupStorageRequest,
    result: _StartupStorageResult,
    writer_factory: StorageWriterFactory | None,
) -> None:
    """Persist one FE-visible startup snapshot when RDB mode is enabled."""
    if request.storage_config is None:
        return
    factory = SqlAlchemyRdbStorageWriter if writer_factory is None else writer_factory
    writer = factory(request.storage_config)
    current_stage, progress_percent = _run_progress(result)
    writer.persist(
        build_storage_write_request(
            StartupStorageSnapshot(
                request.storage_config,
                request.project_name,
                str(request.input_image_folder),
                str(request.output_root),
                len(request.image_paths),
                request.dry_run,
                request.device,
                _resolved_device(request),
                result.status.value,
                current_stage,
                progress_percent,
            )
        )
    )


# RDB 스냅샷에 기록할 실제 device 값을 결정한다. persist_startup_storage에서
# 호출된다.
def _resolved_device(request: _StartupStorageRequest) -> str | None:
    # "auto"는 preprocessing이 실행되어 manifest에 기록해야 실제 백엔드가
    # 정해지므로, 확정되지 않은 요청값 대신 그 값을 스냅샷에 기록해야 한다.
    if request.device != "auto":
        return request.device
    if request.dry_run:
        return None
    manifest_path = (
        request.stage_paths.preprocessing
        / "manifests"
        / "real_preprocessing_manifest.json"
    )
    try:
        payload = parse_json_object(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    device = payload.get("device")
    if not isinstance(device, str) or not device.strip():
        field = "preprocessing_manifest.device"
        reason = "must be a non-empty string"
        raise ContractValidationError(field, reason)
    return device


# 스테이지 실행 결과를 FE에 보여줄 (현재 스테이지, 진행률%) 쌍으로 변환한다.
# persist_startup_storage에서 스냅샷을 쓰기 전에 호출된다.
def _run_progress(result: _StartupStorageResult) -> tuple[str | None, int]:
    match result.status:
        case StartupStatus.COMPLETED:
            return None, 100
        case StartupStatus.FAILED:
            if result.failed_stage is None:
                return None, 0
            return (
                result.failed_stage,
                STAGE_NAMES.index(result.failed_stage) * 100 // len(STAGE_NAMES),
            )
        case StartupStatus.SKIPPED:
            return None, 0
