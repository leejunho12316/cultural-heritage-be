from __future__ import annotations

from decimal import Decimal

from modules.shared import (
    BUDGET_APPROVAL_REQUEST_SCHEMA_VERSION,
    BUDGET_APPROVAL_SCHEMA_VERSION,
    BUDGET_THRESHOLDS,
    DryRunHashInput,
    FollowupHash,
    ImageId,
    LaneConfigVersion,
    PlannedCountHashInput,
    RagLane,
    RuntimeMetadata,
    SourceImageManifestItem,
    budget_approval_matches_request,
    budget_thresholds_exceeded,
    canonical_decimal,
    dry_run_id,
    make_budget_approval_request,
    planned_count_hash,
    source_image_manifest_canonical_json,
    source_image_manifest_sha256,
    user_budget_approval,
)


def _runtime_metadata(*, timestamp: str, output_dir: str) -> RuntimeMetadata:
    return RuntimeMetadata(
        recorded_at=timestamp,
        output_dir=output_dir,
        hostname="host-a",
        temporary_dir="workspace-temp/runtime-a",
        process_id=123,
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


def test_source_image_manifest_hash_serializes_sorted_relative_pairs_only() -> None:
    # Given: the exact dry-run subset in reverse lexical order.
    items = (
        SourceImageManifestItem(
            relative_image_path="assets/b.jpg", file_content_hash="b-sha"
        ),
        SourceImageManifestItem(
            relative_image_path="assets/a.jpg", file_content_hash="a-sha"
        ),
    )

    # When: its canonical serialized field and digest are produced.
    serialized = source_image_manifest_canonical_json(items)
    digest = source_image_manifest_sha256(items)

    # Then: only sorted relative paths and content digests participate.
    assert serialized == (
        '{"images":[{"file_content_hash":"a-sha",'
        '"relative_image_path":"assets/a.jpg"},{"file_content_hash":"b-sha",'
        '"relative_image_path":"assets/b.jpg"}]}'
    )
    assert digest == source_image_manifest_sha256(tuple(reversed(items)))
    assert digest != source_image_manifest_sha256(
        (
            SourceImageManifestItem(
                relative_image_path="assets/a.jpg", file_content_hash="changed-sha"
            ),
        )
    )


def test_canonical_hashes_are_stable_sensitive_and_exclude_runtime_metadata() -> None:
    # Given: equivalent planning inputs with intentionally different runtime metadata.
    first = _dry_run_input(
        _runtime_metadata(timestamp="2026-07-31T00:00:00Z", output_dir="runs/one")
    )
    second = _dry_run_input(
        _runtime_metadata(timestamp="2030-01-01T00:00:00Z", output_dir="runs/two")
    )

    # When: dry-run and planned-count identities are independently derived.
    first_dry_run_id = dry_run_id(first)
    second_dry_run_id = dry_run_id(second)
    counts = BUDGET_THRESHOLDS.within_limits_counts()
    planned = PlannedCountHashInput(
        dry_run_id=first_dry_run_id,
        image_subset_ids=first.image_subset_ids,
        followup_request_hash=first.followup_request_hash,
        counts=counts,
        exceeded_thresholds=(),
        threshold_cap_config_version=first.threshold_cap_config_version,
        runtime_metadata=first.runtime_metadata,
    )

    # Then: excluded runtime values do not change hashes, while relevant values do.
    assert first_dry_run_id == second_dry_run_id
    assert canonical_decimal(Decimal("1.2")) == "1.200000"
    assert planned_count_hash(planned) == planned_count_hash(planned)
    assert dry_run_id(
        DryRunHashInput(
            image_subset_ids=first.image_subset_ids,
            source_image_manifest_sha256=source_image_manifest_sha256(
                (
                    SourceImageManifestItem(
                        relative_image_path="assets/changed.jpg",
                        file_content_hash="changed-manifest-sha",
                    ),
                )
            ),
            followup_request_hash=first.followup_request_hash,
            active_rag_lanes=first.active_rag_lanes,
            corpus_id=first.corpus_id,
            prompt_pack_ids=first.prompt_pack_ids,
            threshold_cap_config_version=first.threshold_cap_config_version,
            tile_view_planner_config_version=first.tile_view_planner_config_version,
            coordinate_transform_version=first.coordinate_transform_version,
            active_rag_lane_config_versions=first.active_rag_lane_config_versions,
            runtime_metadata=second.runtime_metadata,
        )
    ) != first_dry_run_id


def test_budget_thresholds_and_stale_approval_predicates_are_exact() -> None:
    # Given: planned work exceeds every approval threshold.
    exceeded_counts = BUDGET_THRESHOLDS.exceeding_all_counts()
    dry_run = _dry_run_input(
        _runtime_metadata(timestamp="2026-07-31T00:00:00Z", output_dir="r")
    )
    planned = PlannedCountHashInput(
        dry_run_id=dry_run_id(dry_run),
        image_subset_ids=dry_run.image_subset_ids,
        followup_request_hash=FollowupHash("followup-sha"),
        counts=exceeded_counts,
        exceeded_thresholds=budget_thresholds_exceeded(exceeded_counts),
        threshold_cap_config_version=dry_run.threshold_cap_config_version,
        runtime_metadata=dry_run.runtime_metadata,
    )
    request = make_budget_approval_request(planned)
    approval = user_budget_approval(request, approved_at="2026-07-31T01:00:00Z")

    # When: schemas and identity predicates evaluate the current inputs.
    expected_limits = (500, 500, 1000, 1500, 5, 3)

    # Then: thresholds match the plan and mismatched hashes are stale.
    assert BUDGET_THRESHOLDS.as_tuple() == expected_limits
    assert request.schema_version == BUDGET_APPROVAL_REQUEST_SCHEMA_VERSION
    assert approval.schema_version == BUDGET_APPROVAL_SCHEMA_VERSION
    assert request.requires_user_budget_approval is True
    assert budget_approval_matches_request(approval, request)
    assert not budget_approval_matches_request(
        approval, make_budget_approval_request(
            PlannedCountHashInput(
                dry_run_id=request.dry_run_id,
                image_subset_ids=request.image_subset_ids,
                followup_request_hash=FollowupHash("changed-followup-sha"),
                counts=request.planned_counts,
                exceeded_thresholds=request.exceeded_thresholds,
                threshold_cap_config_version="thresholds-v1",
                runtime_metadata=dry_run.runtime_metadata,
            )
        )
    )
