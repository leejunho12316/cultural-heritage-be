"""Canonical JSON hashing and deterministic planning identities."""

import json
from decimal import ROUND_HALF_EVEN, Decimal
from hashlib import sha256

from modules.shared.constants import NORMALIZED_DECIMAL_PLACES
from modules.shared.hash_inputs import (
    DryRunHashInput,
    PlannedCountHashInput,
    SourceImageManifestItem,
)
from modules.shared.models import (
    BudgetCounts,
    BudgetThreshold,
    DryRunId,
    FollowupHash,
    PlannedCountHash,
    SourceImageManifestHash,
    UserFollowupRequest,
)


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _array(items: tuple[str, ...]) -> str:
    return f"[{','.join(items)}]"


def _record(fields: tuple[tuple[str, str], ...]) -> str:
    entries = (f"{_quoted(key)}:{value}" for key, value in sorted(fields))
    return "{" + ",".join(entries) + "}"


def _quoted_sorted(values: tuple[str, ...]) -> str:
    return _array(tuple(_quoted(value) for value in sorted(values)))


def _thresholds_json(thresholds: tuple[BudgetThreshold, ...]) -> str:
    return _quoted_sorted(tuple(threshold.value for threshold in thresholds))


def canonical_decimal(value: Decimal) -> str:
    """Normalize decimal values to the shared fixed hash precision."""
    exponent = Decimal(1).scaleb(-NORMALIZED_DECIMAL_PLACES)
    return format(value.quantize(exponent, rounding=ROUND_HALF_EVEN), "f")


def _sha256(canonical_json: str) -> str:
    return sha256(canonical_json.encode("utf-8")).hexdigest()


def source_image_manifest_canonical_json(
    items: tuple[SourceImageManifestItem, ...],
) -> str:
    """Serialize sorted relative-image and content-digest pairs only."""
    serialized_items = tuple(
        _record(
            (
                ("file_content_hash", _quoted(item.file_content_hash)),
                ("relative_image_path", _quoted(item.relative_image_path)),
            )
        )
        for item in sorted(items, key=lambda item: item.relative_image_path)
    )
    return _record((("images", _array(serialized_items)),))


def source_image_manifest_sha256(
    items: tuple[SourceImageManifestItem, ...],
) -> SourceImageManifestHash:
    """Hash the canonical source-image manifest serialization."""
    return SourceImageManifestHash(_sha256(source_image_manifest_canonical_json(items)))


def _followup_record(request: UserFollowupRequest) -> str:
    budget_scope = _quoted_sorted(request.budget_scope)
    provenance = _quoted_sorted(request.provenance)
    return _record(
        (
            ("budget_scope", budget_scope),
            ("dry_run_only", "true" if request.dry_run_only else "false"),
            ("followup_reason", _quoted(request.followup_reason)),
            ("provenance", provenance),
            ("request_id", _quoted(request.request_id)),
            ("requester", _quoted(request.requester)),
            ("selector_id", _quoted(request.selector_id)),
            ("selector_type", _quoted(request.selector_type.value)),
            ("trigger_priority", str(request.trigger_priority)),
        )
    )


def followup_request_hash(
    requests: tuple[UserFollowupRequest, ...],
) -> FollowupHash:
    """Hash sorted normalized user requests before parent-target resolution."""
    request_records = tuple(sorted(_followup_record(request) for request in requests))
    canonical_json = _record((("requests", _array(request_records)),))
    return FollowupHash(_sha256(canonical_json))


def stable_empty_followup_request_hash() -> FollowupHash:
    """Return the stable normalized hash used when no follow-up exists."""
    return followup_request_hash(())


def _counts_record(counts: BudgetCounts) -> str:
    return _record(
        (
            ("estimated_output_bytes", str(counts.estimated_output_bytes)),
            ("model_invocations", str(counts.model_invocations)),
            ("prompt_variants", str(counts.prompt_variants)),
            ("sam2_calls", str(counts.sam2_calls)),
            ("smoke_images", str(counts.smoke_images)),
            ("tiles", str(counts.tiles)),
        )
    )


def dry_run_id(value: DryRunHashInput) -> DryRunId:
    """Hash the exact dry-run planning inputs, excluding runtime metadata."""
    lane_versions = tuple(
        _record(
            (("lane", _quoted(item.lane.value)), ("version", _quoted(item.version)))
        )
        for item in sorted(
            value.active_rag_lane_config_versions, key=lambda item: item.lane.value
        )
    )
    active_lanes = _quoted_sorted(tuple(item.value for item in value.active_rag_lanes))
    image_ids = _quoted_sorted(value.image_subset_ids)
    prompt_packs = _quoted_sorted(value.prompt_pack_ids)
    canonical_json = _record(
        (
            ("active_rag_lane_config_versions", _array(lane_versions)),
            ("active_rag_lanes", active_lanes),
            (
                "coordinate_transform_version",
                _quoted(value.coordinate_transform_version),
            ),
            ("corpus_id", _quoted(value.corpus_id)),
            ("followup_request_hash", _quoted(value.followup_request_hash)),
            ("image_subset_ids", image_ids),
            ("prompt_pack_ids", prompt_packs),
            (
                "source_image_manifest_sha256",
                _quoted(value.source_image_manifest_sha256),
            ),
            (
                "threshold_cap_config_version",
                _quoted(value.threshold_cap_config_version),
            ),
            (
                "tile_view_planner_config_version",
                _quoted(value.tile_view_planner_config_version),
            ),
        )
    )
    return DryRunId(_sha256(canonical_json))


def planned_count_hash(value: PlannedCountHashInput) -> PlannedCountHash:
    """Hash deterministic count inputs, excluding runtime metadata."""
    image_ids = _quoted_sorted(value.image_subset_ids)
    thresholds = _thresholds_json(value.exceeded_thresholds)
    canonical_json = _record(
        (
            ("counts", _counts_record(value.counts)),
            ("dry_run_id", _quoted(value.dry_run_id)),
            ("exceeded_thresholds", thresholds),
            ("followup_request_hash", _quoted(value.followup_request_hash)),
            ("image_subset_ids", image_ids),
            (
                "threshold_cap_config_version",
                _quoted(value.threshold_cap_config_version),
            ),
        )
    )
    return PlannedCountHash(_sha256(canonical_json))
