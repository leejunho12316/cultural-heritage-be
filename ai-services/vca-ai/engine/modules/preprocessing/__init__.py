"""Safe CLI input preparation and object/scale-aware view planning contracts."""

from modules.preprocessing.contracts.views import (
    MIN_OBJECT_TILE_OVERLAP,
    BoundingBox,
    CoordinateTransform,
    LaneTilePlan,
    ObjectRankingHints,
    ObjectTarget,
    ScaleConfidence,
    ScaleMetadata,
    TileRankingMetadata,
    ViewKind,
    ViewManifest,
    ViewPlanningRequest,
    ViewRecord,
    ViewReuseMode,
)
from modules.preprocessing.preflight.checks import (
    PreflightFailureReceipt,
    PreflightImage,
    PreflightIssue,
    PreflightReceipt,
    PreflightSettings,
    Readability,
    preflight_run,
)
from modules.preprocessing.preflight.manifest import (
    InputManifest,
    InputManifestImage,
    build_input_manifest,
)
from modules.preprocessing.preflight.request import (
    AssetPolicy,
    Device,
    RunRequest,
    parse_run_request,
)
from modules.preprocessing.views.planner import plan_views
from modules.preprocessing.views.tiling import OVERLAP_RATIO
from modules.shared import ContractValidationError, DetectorLane, ExitCode, ImageId

__all__ = (
    "MIN_OBJECT_TILE_OVERLAP",
    "OVERLAP_RATIO",
    "AssetPolicy",
    "BoundingBox",
    "ContractValidationError",
    "CoordinateTransform",
    "DetectorLane",
    "Device",
    "ExitCode",
    "ImageId",
    "InputManifest",
    "InputManifestImage",
    "LaneTilePlan",
    "ObjectRankingHints",
    "ObjectTarget",
    "PreflightFailureReceipt",
    "PreflightImage",
    "PreflightIssue",
    "PreflightReceipt",
    "PreflightSettings",
    "Readability",
    "RunRequest",
    "ScaleConfidence",
    "ScaleMetadata",
    "TileRankingMetadata",
    "ViewKind",
    "ViewManifest",
    "ViewPlanningRequest",
    "ViewRecord",
    "ViewReuseMode",
    "build_input_manifest",
    "parse_run_request",
    "plan_views",
    "preflight_run",
)
