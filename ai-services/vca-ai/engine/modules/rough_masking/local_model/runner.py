"""Local model runner factory for active rough-mask lanes."""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from modules.preprocessing.model_runtime.inventory import ModelInventoryEntry
from modules.rough_masking.artifacts.materialization import materialize_anomaly_outputs
from modules.rough_masking.contracts import AdapterRequest, RunnerOutcome
from modules.rough_masking.local_model.runtime import LaneRuntime, build_lane_runtime
from modules.shared import (
    ContractValidationError,
    DetectorLane,
    validate_active_detector_lanes,
)

DETECTOR_MODEL_KEYS: Final[dict[DetectorLane, str]] = {
    DetectorLane.OWLV2_SAM2: "owlv2_sam2.detector",
    DetectorLane.GROUNDED_SAM2: "grounded_sam2.detector",
}
SAM2_MODEL_KEY: Final = "sam2.segmenter"
DEFAULT_MODEL_CACHE_ROOT: Final = Path("models")
_SNAPSHOT_HASH_EXCLUDED_DIRS: Final = frozenset({".cache"})
EXPECTED_MODEL_REPO_IDS: Final[dict[str, str]] = {
    "owlv2_sam2.detector": "google/owlv2-base-patch16-ensemble",
    "grounded_sam2.detector": "IDEA-Research/grounding-dino-base",
    SAM2_MODEL_KEY: "facebook/sam2-hiera-large",
}
EXPECTED_MODEL_REVISIONS: Final[dict[str, str]] = {
    "owlv2_sam2.detector": (
        "sha256:b0e1c87eeecad23b816cd1f26a6ba14bb54d41a399c895ce4ae73ae5125bd44a"
    ),
    "grounded_sam2.detector": (
        "sha256:e3f2355ee634388c17c5fb1c522b64a2870a0f0b88016f224a0c1305bf67069c"
    ),
    SAM2_MODEL_KEY: (
        "sha256:f41466bb56f059614f97e14b71c63f84a2b96022175d26a9fd6291b40be7fd5a"
    ),
}


@dataclass(frozen=True, slots=True)
class LocalModelCachePolicy:
    """Trust policy for local model cache validation before remote-code loading."""

    root: Path = DEFAULT_MODEL_CACHE_ROOT
    verify_hashes: bool = True


# build_local_model_runner는 object-view + tile-view마다(런당 최대 수백 회)
# 다시 호출되고, 그때마다 _entry()가 같은 디렉터리를 다시 통째로 해싱한다.
# 콘텐츠는 런 도중 바뀌지 않으므로, (key, 로컬 경로, revision) 조합을 한 번
# 검증에 성공하면 같은 프로세스 안에서는 재해싱을 건너뛴다.
_VERIFIED_SNAPSHOT_REVISIONS: set[tuple[str, str, str]] = set()


def _contained(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _snapshot_revision(local_dir: Path, cache_root: Path) -> str:
    # huggingface_hub writes its own download bookkeeping (locks, ETag
    # metadata) under a ".cache" subdirectory; it is not model content and
    # can be regenerated with different bytes on a later download, so it
    # must not affect the pinned content hash.
    snapshot_hash = hashlib.sha256()
    files = sorted(
        candidate
        for candidate in local_dir.rglob("*")
        if candidate.is_file()
        and _SNAPSHOT_HASH_EXCLUDED_DIRS.isdisjoint(
            candidate.relative_to(local_dir).parts
        )
    )
    for path in files:
        resolved_path = path.resolve()
        if not _contained(resolved_path, cache_root):
            field = "model_inventory.models.local_dir"
            reason = "local cache file escapes model cache root"
            raise ContractValidationError(field, reason)
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        snapshot_hash.update(str(path.relative_to(local_dir)).encode())
        snapshot_hash.update(b"\0")
        snapshot_hash.update(file_hash.encode())
        snapshot_hash.update(b"\n")
    return f"sha256:{snapshot_hash.hexdigest()}"


# 모델 인벤토리 항목이 기대한 repo_id/revision과 일치하고, 로컬 캐시 경로가
# cache_root 밖으로 벗어나지 않는지 검증한다. build_local_model_runner에서
# detector/SAM2 항목 각각에 대해 호출된다.
def _entry(
    entries: dict[str, ModelInventoryEntry],
    key: str,
    model_cache_root: Path,
    *,
    verify_model_hashes: bool,
) -> ModelInventoryEntry:
    entry = entries.get(key)
    if entry is None:
        field = "model_inventory.models"
        reason = f"missing required key: {key}"
        raise ContractValidationError(field, reason)
    if entry.key != key:
        field = f"model_inventory.models.{key}.key"
        reason = "entry key mismatch"
        raise ContractValidationError(field, reason)
    if entry.repo_id != EXPECTED_MODEL_REPO_IDS[key]:
        field = f"model_inventory.models.{key}.repo_id"
        reason = "unexpected model repository"
        raise ContractValidationError(field, reason)
    if entry.revision != EXPECTED_MODEL_REVISIONS[key]:
        field = f"model_inventory.models.{key}.revision"
        reason = "unexpected model snapshot revision"
        raise ContractValidationError(field, reason)
    if not entry.local_dir.exists():
        field = f"model_inventory.models.{key}.local_dir"
        reason = "local cache path does not exist"
        raise ContractValidationError(field, reason)
    cache_root = model_cache_root.expanduser().resolve()
    local_dir = entry.local_dir.expanduser().resolve()
    if not (local_dir == cache_root or local_dir.is_relative_to(cache_root)):
        field = f"model_inventory.models.{key}.local_dir"
        reason = "local cache path escapes model cache root"
        raise ContractValidationError(field, reason)
    if verify_model_hashes:
        verification_key = (key, str(local_dir), entry.revision)
        if verification_key not in _VERIFIED_SNAPSHOT_REVISIONS:
            if _snapshot_revision(local_dir, cache_root) != entry.revision:
                field = f"model_inventory.models.{key}.revision"
                reason = "local cache content hash mismatch"
                raise ContractValidationError(field, reason)
            _VERIFIED_SNAPSHOT_REVISIONS.add(verification_key)
    return entry


@dataclass(frozen=True, slots=True)
class LocalModelRunner:
    """DetectorRunner backed by one local detector/SAM2 runtime."""

    lane: DetectorLane
    image_path: Path
    runtime: LaneRuntime

    def __call__(self, request: AdapterRequest) -> RunnerOutcome:
        """Run local inference and materialize anomaly rough-mask outputs."""
        if request.lane is not self.lane:
            field = "lane"
            reason = "request lane must match local runner lane"
            raise ContractValidationError(field, reason)
        outputs = self.runtime.detect(request, self.image_path)
        materialize_anomaly_outputs(request, outputs, self.image_path)
        return RunnerOutcome(runner_invoked=True)


def build_local_model_runner(
    *,
    lane: DetectorLane,
    image_path: Path,
    model_entries: dict[str, ModelInventoryEntry],
    device: str,
    model_cache_policy: LocalModelCachePolicy | None = None,
) -> LocalModelRunner:
    """Build a local-only rough-mask runner for one active detector lane."""
    _ = validate_active_detector_lanes((lane,))
    detector_key = DETECTOR_MODEL_KEYS.get(lane)
    if detector_key is None:
        field = "lane"
        reason = "no local model runner for lane"
        raise ContractValidationError(field, reason)
    if not image_path.is_file():
        field = "view_image_path"
        reason = "ROI image does not exist"
        raise ContractValidationError(field, reason)
    cache_policy = model_cache_policy or LocalModelCachePolicy()
    runtime = build_lane_runtime(
        lane=lane,
        detector_entry=_entry(
            model_entries,
            detector_key,
            cache_policy.root,
            verify_model_hashes=cache_policy.verify_hashes,
        ),
        sam2_entry=_entry(
            model_entries,
            SAM2_MODEL_KEY,
            cache_policy.root,
            verify_model_hashes=cache_policy.verify_hashes,
        ),
        device=device,
    )
    return LocalModelRunner(lane, image_path, runtime)
