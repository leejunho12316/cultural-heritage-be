import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.services.assessment_models import MaxImages, RunTimeoutSeconds


class VcaDevice(StrEnum):
    AUTO = "auto"
    CUDA = "cuda"
    MPS = "mps"
    CPU = "cpu"


class VcaRunMode(StrEnum):
    REAL = "real"
    DRY_RUN = "dry-run"


@dataclass(frozen=True, slots=True)
class VcaRuntimeSettingsError(Exception):
    reason: str

    def __str__(self) -> str:
        return f"Invalid VCA runtime settings: {self.reason}"


@dataclass(frozen=True, slots=True)
class VcaRuntimeSettings:
    shared_storage_root: Path
    engine_root: Path
    timeout_seconds: RunTimeoutSeconds
    run_mode: VcaRunMode
    device: VcaDevice | None
    max_images: MaxImages | None
    model_cache_root: Path | None
    allow_unverified_model_hashes: bool


# 환경변수들로부터 VcaRuntimeSettings를 조립한다. create_assessment_run(),
# get_assessment_progress(), get_assessment_report() 등 실행 설정이
# 필요한 곳에서 매 호출마다 새로 읽는다.
def runtime_settings_from_env() -> VcaRuntimeSettings:
    return VcaRuntimeSettings(
        shared_storage_root=Path(os.environ.get("VCA_SHARED_STORAGE_ROOT", "/shared/vca")),
        engine_root=Path(os.environ.get("VCA_ENGINE_ROOT", "/vca_v2")),
        timeout_seconds=_run_timeout_seconds_from_env(),
        run_mode=_run_mode_from_env(),
        device=_device_from_env(),
        max_images=_max_images_from_env(),
        model_cache_root=_model_cache_root_from_env(),
        allow_unverified_model_hashes=_allow_unverified_model_hashes_from_env(),
    )


# VCA_RUN_TIMEOUT_SECONDS 환경변수를 읽어 파이프라인 실행 제한시간을
# 정한다. runtime_settings_from_env()에서 호출된다.
def _run_timeout_seconds_from_env() -> RunTimeoutSeconds:
    # VCA_DRY_RUN_TIMEOUT_SECONDS는 예전 변수명으로, 아직 이름을 바꾸지
    # 않은 배포 환경도 타임아웃 값을 받을 수 있도록 fallback으로 남겨둔다.
    raw_timeout = os.environ.get(
        "VCA_RUN_TIMEOUT_SECONDS",
        os.environ.get("VCA_DRY_RUN_TIMEOUT_SECONDS", "120"),
    )
    try:
        timeout_seconds = int(raw_timeout)
    except ValueError as error:
        raise VcaRuntimeSettingsError("VCA_RUN_TIMEOUT_SECONDS must be an integer") from error
    if timeout_seconds < 1:
        raise VcaRuntimeSettingsError("VCA_RUN_TIMEOUT_SECONDS must be positive")
    return RunTimeoutSeconds(timeout_seconds)


# VCA_RUN_MODE 환경변수를 real/dry-run 중 하나로 해석한다.
def _run_mode_from_env() -> VcaRunMode:
    raw_run_mode = os.environ.get("VCA_RUN_MODE", VcaRunMode.REAL)
    try:
        return VcaRunMode(raw_run_mode)
    except ValueError as error:
        raise VcaRuntimeSettingsError(
            "VCA_RUN_MODE must be real or dry-run"
        ) from error


# VCA_DEVICE 환경변수를 읽어 추론 디바이스를 정한다. 지정하지 않으면
# None을 반환해 엔진이 auto로 판단하게 둔다.
def _device_from_env() -> VcaDevice | None:
    raw_device = os.environ.get("VCA_DEVICE")
    if raw_device is None:
        return None
    try:
        return VcaDevice(raw_device)
    except ValueError as error:
        raise VcaRuntimeSettingsError("VCA_DEVICE must be auto, cuda, mps, or cpu") from error


# VCA_MODEL_CACHE_ROOT 환경변수를 모델 캐시 경로로 해석한다.
def _model_cache_root_from_env() -> Path | None:
    raw_model_cache_root = os.environ.get("VCA_MODEL_CACHE_ROOT")
    if raw_model_cache_root is None:
        return None
    if not raw_model_cache_root.strip():
        raise VcaRuntimeSettingsError("VCA_MODEL_CACHE_ROOT must not be blank")
    return Path(raw_model_cache_root)


# VCA_LOCAL_ALLOW_UNVERIFIED_MODEL_HASHES 환경변수 값이다. 로컬 개발용
# 우회 옵션이므로 기본값은 항상 false다.
def _allow_unverified_model_hashes_from_env() -> bool:
    return os.environ.get("VCA_LOCAL_ALLOW_UNVERIFIED_MODEL_HASHES") == "true"


# VCA_MAX_IMAGES 환경변수를 읽는다. "all" 또는 양의 정수 문자열만
# 허용한다.
def _max_images_from_env() -> MaxImages | None:
    raw_max_images = os.environ.get("VCA_MAX_IMAGES")
    if raw_max_images is None:
        return None
    if raw_max_images == "all" or (
        raw_max_images.isdecimal() and int(raw_max_images) > 0
    ):
        return MaxImages(raw_max_images)
    raise VcaRuntimeSettingsError("VCA_MAX_IMAGES must be all or a positive integer")
