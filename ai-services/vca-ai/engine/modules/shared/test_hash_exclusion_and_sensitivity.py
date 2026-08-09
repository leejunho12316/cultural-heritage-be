from __future__ import annotations

from modules.shared import (
    BUDGET_THRESHOLDS,
    BudgetCounts,
    DryRunHashInput,
    FollowupHash,
    ImageId,
    LaneConfigVersion,
    PlannedCountHashInput,
    RagLane,
    RuntimeMetadata,
    dry_run_id,
    planned_count_hash,
    source_image_manifest_sha256,
)


def _dry_run_input(runtime_metadata: RuntimeMetadata) -> DryRunHashInput:
    return DryRunHashInput(
        image_subset_ids=(ImageId("image-b"), ImageId("image-a")),
        source_image_manifest_sha256=source_image_manifest_sha256(()),
        followup_request_hash=FollowupHash("followup-sha"),
        active_rag_lanes=(RagLane.GROUNDINGDINO, RagLane.OWLV2),
        corpus_id="corpus-001",
        prompt_pack_ids=("rag-pack", "seed-pack"),
        threshold_cap_config_version="thresholds-v1",
        tile_view_planner_config_version="tiles-v1",
        coordinate_transform_version="coordinates-v1",
        active_rag_lane_config_versions=(
            LaneConfigVersion(lane=RagLane.GROUNDINGDINO, version="gd-v1"),
            LaneConfigVersion(lane=RagLane.OWLV2, version="owl-v1"),
        ),
        runtime_metadata=runtime_metadata,
    )


def test_planning_hashes_exclude_all_runtime_metadata_fields() -> None:
    # Given: planning inputs that differ only by excluded runtime metadata fields.
    first = _dry_run_input(
        RuntimeMetadata(
            recorded_at="2026-07-31T00:00:00Z",
            output_dir="runs/one",
            hostname="host-a",
            temporary_dir="workspace-temp/runtime-a",
            process_id=123,
        )
    )
    second = _dry_run_input(
        RuntimeMetadata(
            recorded_at="2030-01-01T00:00:00Z",
            output_dir="runs/two",
            hostname="host-b",
            temporary_dir="workspace-temp/runtime-b",
            process_id=999,
        )
    )
    first_dry_run_id = dry_run_id(first)
    second_dry_run_id = dry_run_id(second)
    counts = BUDGET_THRESHOLDS.within_limits_counts()
    first_planned = PlannedCountHashInput(
        dry_run_id=first_dry_run_id,
        image_subset_ids=first.image_subset_ids,
        followup_request_hash=first.followup_request_hash,
        counts=counts,
        exceeded_thresholds=(),
        threshold_cap_config_version=first.threshold_cap_config_version,
        runtime_metadata=first.runtime_metadata,
    )
    second_planned = PlannedCountHashInput(
        dry_run_id=second_dry_run_id,
        image_subset_ids=second.image_subset_ids,
        followup_request_hash=second.followup_request_hash,
        counts=counts,
        exceeded_thresholds=(),
        threshold_cap_config_version=second.threshold_cap_config_version,
        runtime_metadata=second.runtime_metadata,
    )
    # When: canonical identities are computed from both inputs.
    # Then: timestamp, output dir, host, temp dir, and process id are all excluded.
    assert first_dry_run_id == second_dry_run_id
    assert planned_count_hash(first_planned) == planned_count_hash(second_planned)


def test_planned_count_hash_changes_when_relevant_budget_inputs_change() -> None:
    # Given: two planned-count inputs that differ in planned model invocations.
    dry_run = _dry_run_input(
        RuntimeMetadata(
            recorded_at="2026-07-31T00:00:00Z",
            output_dir="runs/current",
            hostname="host-a",
            temporary_dir="workspace-temp/runtime-a",
            process_id=123,
        )
    )
    base_counts = BUDGET_THRESHOLDS.within_limits_counts()
    changed_counts = BudgetCounts(1, 0, 0, 0, 0, 0)
    base = PlannedCountHashInput(
        dry_run_id=dry_run_id(dry_run),
        image_subset_ids=dry_run.image_subset_ids,
        followup_request_hash=dry_run.followup_request_hash,
        counts=base_counts,
        exceeded_thresholds=(),
        threshold_cap_config_version=dry_run.threshold_cap_config_version,
        runtime_metadata=dry_run.runtime_metadata,
    )
    changed = PlannedCountHashInput(
        dry_run_id=base.dry_run_id,
        image_subset_ids=base.image_subset_ids,
        followup_request_hash=base.followup_request_hash,
        counts=changed_counts,
        exceeded_thresholds=(),
        threshold_cap_config_version=base.threshold_cap_config_version,
        runtime_metadata=base.runtime_metadata,
    )

    # When: planned-count identities are computed.
    # Then: budget-relevant counts perturb approval identity.
    assert planned_count_hash(base) != planned_count_hash(changed)
