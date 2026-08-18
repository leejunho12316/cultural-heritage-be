"""Anomaly rough-mask lane contracts and candidate normalization."""

from modules.rough_masking.artifacts.materialization import (
    AnomalyMaskOutput,
    MaskOutput,
    RejectedMaskOutput,
    materialize_anomaly_outputs,
)
from modules.rough_masking.candidates.normalization import (
    AdapterReceipt,
    AssetReference,
    CandidateStatus,
    NoFakeClaimAudit,
    RawDetectorCandidate,
    execute_adapter,
)
from modules.rough_masking.contracts import (
    AdapterRequest,
    DetectorRunner,
    ImageDimensions,
    RunnerOutcome,
    SeedRequestPaths,
    SeedThresholds,
    build_roi_seed_request,
    build_seed_request,
    seed_thresholds,
)
from modules.rough_masking.coordinates import (
    candidate_view_transform,
    restore_original_bbox,
    restore_original_mask,
)
from modules.rough_masking.local_model.runner import (
    DETECTOR_MODEL_KEYS,
    SAM2_MODEL_KEY,
    LocalModelCachePolicy,
    LocalModelRunner,
    build_local_model_runner,
)
from modules.rough_masking.routing import (
    RoiRoutingInput,
    build_lane_roi_seed_requests,
    seed_paths_from_preprocessing_object,
)

__all__ = (
    "DETECTOR_MODEL_KEYS",
    "SAM2_MODEL_KEY",
    "AdapterReceipt",
    "AdapterRequest",
    "AnomalyMaskOutput",
    "AssetReference",
    "CandidateStatus",
    "DetectorRunner",
    "ImageDimensions",
    "LocalModelCachePolicy",
    "LocalModelRunner",
    "MaskOutput",
    "NoFakeClaimAudit",
    "RawDetectorCandidate",
    "RejectedMaskOutput",
    "RoiRoutingInput",
    "RunnerOutcome",
    "SeedRequestPaths",
    "SeedThresholds",
    "build_lane_roi_seed_requests",
    "build_local_model_runner",
    "build_roi_seed_request",
    "build_seed_request",
    "candidate_view_transform",
    "execute_adapter",
    "materialize_anomaly_outputs",
    "restore_original_bbox",
    "restore_original_mask",
    "seed_paths_from_preprocessing_object",
    "seed_thresholds",
)
