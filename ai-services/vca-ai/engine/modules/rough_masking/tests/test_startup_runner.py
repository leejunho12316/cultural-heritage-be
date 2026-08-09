from __future__ import annotations

import json
from typing import TYPE_CHECKING

from modules.rough_masking import RunnerOutcome
from modules.rough_masking.candidates.normalization import (
    AdapterReceipt,
    NoFakeClaimAudit,
)
from modules.rough_masking.startup_runner import run_rough_masking_stage
from modules.rough_masking.tests._startup_runner_support import (
    make_candidate,
    make_request,
    write_dry_manifest,
    write_model_inventory,
    write_real_manifest_assets,
)
from modules.shared import (
    DetectorLane,
    LaneExecutionStatus,
)

if TYPE_CHECKING:
    from pathlib import Path

    from modules.preprocessing.model_runtime.inventory import ModelInventoryEntry
    from modules.rough_masking import AdapterRequest, DetectorRunner
    from modules.rough_masking.local_model.runner import LocalModelCachePolicy


def test_startup_runner_dry_run_validates_manifest_without_model_calls(
    tmp_path: Path,
) -> None:
    # Given: preprocessing emitted the dry-run manifest contract.
    stage_request = make_request(tmp_path, dry_run=True)
    write_dry_manifest(stage_request)
    model_calls: list[DetectorLane] = []
    adapter_calls: list[DetectorLane] = []

    def runner_factory(
        *,
        lane: DetectorLane,
        image_path: Path,
        model_entries: dict[str, ModelInventoryEntry],
        device: str,
        model_cache_policy: object | None = None,
    ) -> DetectorRunner:
        _ = (image_path, model_entries, device, model_cache_policy)
        model_calls.append(lane)

        def runner(request: AdapterRequest) -> RunnerOutcome:
            _ = request
            return RunnerOutcome(runner_invoked=True)

        return runner

    def adapter_executor(
        request: AdapterRequest, runner: DetectorRunner
    ) -> AdapterReceipt:
        _ = runner
        adapter_calls.append(request.lane)
        return AdapterReceipt(
            request.lane.value,
            LaneExecutionStatus.REAL_EXECUTED,
            (),
            (),
            NoFakeClaimAudit(
                active_lane_only=True,
                fabricated_candidate_count=0,
                runner_invoked=True,
            ),
        )

    # When: rough masking runs in dry-run mode.
    exit_code = run_rough_masking_stage(
        stage_request,
        runner_factory=runner_factory,
        adapter_executor=adapter_executor,
    )

    # Then: it succeeds without invoking any model-backed seam.
    assert exit_code == 0
    assert model_calls == []
    assert adapter_calls == []


def _never_called_runner_factory(
    *,
    lane: DetectorLane,
    image_path: Path,
    model_entries: dict[str, ModelInventoryEntry],
    device: str,
    model_cache_policy: object | None = None,
) -> DetectorRunner:
    _ = (lane, image_path, model_entries, device, model_cache_policy)
    message = "budget-exceeded run must not reach the detector runner"
    raise AssertionError(message)


def _never_called_adapter_executor(
    request: AdapterRequest, runner: DetectorRunner
) -> AdapterReceipt:
    _ = (request, runner)
    message = "budget-exceeded run must not reach the adapter executor"
    raise AssertionError(message)


def test_rough_masking_blocks_when_tile_budget_exceeded_without_approval(
    tmp_path: Path,
) -> None:
    # Given: preprocessing planned more tiles than the budget threshold and
    # flagged it (D010/T4 tiling can produce far more than a handful of
    # tiles per object), with no approval artifact present yet.
    stage_request = make_request(tmp_path)
    write_real_manifest_assets(
        stage_request, tile_count=5000, requires_user_budget_approval=True
    )
    write_model_inventory(stage_request)

    # When: rough masking runs the project stage.
    exit_code = run_rough_masking_stage(
        stage_request,
        runner_factory=_never_called_runner_factory,
        adapter_executor=_never_called_adapter_executor,
    )

    # Then: it fails before touching any detector runner, and writes a
    # request artifact the user can approve to unblock a rerun.
    assert exit_code != 0
    request_path = stage_request.paths.rough_masking / "budget_approval_request.json"
    assert request_path.is_file()
    written = json.loads(request_path.read_text(encoding="utf-8"))
    assert written["tile_count"] == 5000
    assert written["object_count"] == 1


def test_rough_masking_proceeds_once_a_matching_approval_artifact_exists(
    tmp_path: Path,
) -> None:
    # Given: the same over-budget manifest as above, but this time a
    # tile_count/object_count-matching approval artifact already exists
    # (as if the user wrote it back after seeing the request).
    stage_request = make_request(tmp_path)
    write_real_manifest_assets(
        stage_request, tile_count=5000, requires_user_budget_approval=True
    )
    write_model_inventory(stage_request)
    stage_request.paths.rough_masking.mkdir(parents=True)
    approval_path = stage_request.paths.rough_masking / "budget_approval.json"
    _ = approval_path.write_text(
        json.dumps({"tile_count": 5000, "object_count": 1}), encoding="utf-8"
    )

    def runner_factory(
        *,
        lane: DetectorLane,
        image_path: Path,
        model_entries: dict[str, ModelInventoryEntry],
        device: str,
        model_cache_policy: object | None = None,
    ) -> DetectorRunner:
        _ = (lane, image_path, model_entries, device, model_cache_policy)

        def runner(request: AdapterRequest) -> RunnerOutcome:
            _ = request
            return RunnerOutcome(runner_invoked=True)

        return runner

    def adapter_executor(
        request: AdapterRequest, runner: DetectorRunner
    ) -> AdapterReceipt:
        _ = runner
        return AdapterReceipt(
            request.lane.value,
            LaneExecutionStatus.REAL_EXECUTED,
            (make_candidate(request),),
            (),
            NoFakeClaimAudit(
                active_lane_only=True,
                fabricated_candidate_count=0,
                runner_invoked=True,
            ),
        )

    # When: rough masking runs the project stage.
    exit_code = run_rough_masking_stage(
        stage_request,
        runner_factory=runner_factory,
        adapter_executor=adapter_executor,
    )

    # Then: the matching approval unblocks the run.
    assert exit_code == 0


def test_startup_runner_invokes_only_preprocessing_manifest_lanes(
    tmp_path: Path,
) -> None:
    # Given: one real preprocessing object and a fake adapter seam.
    stage_request = make_request(tmp_path)
    manifest_paths = write_real_manifest_assets(stage_request)
    write_model_inventory(stage_request)
    runner_calls: list[tuple[DetectorLane, Path]] = []
    adapter_requests: list[AdapterRequest] = []

    def runner_factory(
        *,
        lane: DetectorLane,
        image_path: Path,
        model_entries: dict[str, ModelInventoryEntry],
        device: str,
        model_cache_policy: object | None = None,
    ) -> DetectorRunner:
        _ = (model_entries, device, model_cache_policy)
        runner_calls.append((lane, image_path))

        def runner(request: AdapterRequest) -> RunnerOutcome:
            _ = request
            return RunnerOutcome(runner_invoked=True)

        return runner

    def adapter_executor(
        request: AdapterRequest, runner: DetectorRunner
    ) -> AdapterReceipt:
        _ = runner
        adapter_requests.append(request)
        return AdapterReceipt(
            request.lane.value,
            LaneExecutionStatus.REAL_EXECUTED,
            (make_candidate(request),),
            (),
            NoFakeClaimAudit(
                active_lane_only=True,
                fabricated_candidate_count=0,
                runner_invoked=True,
            ),
        )

    # When: rough masking runs the project stage.
    exit_code = run_rough_masking_stage(
        stage_request,
        runner_factory=runner_factory,
        adapter_executor=adapter_executor,
    )

    # Then: only the lane produced by preprocessing is routed through rough
    # masking, once for the object crop and once for its one real tile - the
    # tile file (not the object crop) is used as the tile's own image_path.
    assert exit_code == 0
    assert [lane for lane, _ in runner_calls] == [
        DetectorLane.OWLV2_SAM2,
        DetectorLane.OWLV2_SAM2,
    ]
    assert [image_path for _, image_path in runner_calls] == [
        manifest_paths.crop_path.resolve(),
        manifest_paths.tile_path.resolve(),
    ]
    assert [adapter_request.lane for adapter_request in adapter_requests] == [
        DetectorLane.OWLV2_SAM2,
        DetectorLane.OWLV2_SAM2,
    ]
    assert all(
        adapter_request.view.object_id == "object-001"
        for adapter_request in adapter_requests
    )
    lane_root = (
        stage_request.paths.rough_masking / "owlv2_sam2" / "object-001" / "owlv2_sam2"
    )
    assert [
        adapter_request.lane_output_dir for adapter_request in adapter_requests
    ] == [
        lane_root / "object",
        lane_root / "rough-mask-tile:object-001:000",
    ]


def test_startup_runner_uses_request_model_hash_policy(
    tmp_path: Path,
) -> None:
    # Given: startup explicitly allowed local unverified model cache hashes.
    stage_request = make_request(tmp_path, verify_model_hashes=False)
    _ = write_real_manifest_assets(stage_request)
    write_model_inventory(stage_request)
    cache_policies: list[LocalModelCachePolicy] = []

    def runner_factory(
        *,
        lane: DetectorLane,
        image_path: Path,
        model_entries: dict[str, ModelInventoryEntry],
        device: str,
        model_cache_policy: LocalModelCachePolicy | None = None,
    ) -> DetectorRunner:
        _ = (lane, image_path, model_entries, device)
        if model_cache_policy is not None:
            cache_policies.append(model_cache_policy)

        def runner(request: AdapterRequest) -> RunnerOutcome:
            _ = request
            return RunnerOutcome(runner_invoked=True)

        return runner

    def adapter_executor(
        request: AdapterRequest, runner: DetectorRunner
    ) -> AdapterReceipt:
        _ = runner
        return AdapterReceipt(
            request.lane.value,
            LaneExecutionStatus.REAL_EXECUTED,
            (make_candidate(request),),
            (),
            NoFakeClaimAudit(
                active_lane_only=True,
                fabricated_candidate_count=0,
                runner_invoked=True,
            ),
        )

    # When: rough masking builds its local model runner.
    exit_code = run_rough_masking_stage(
        stage_request,
        runner_factory=runner_factory,
        adapter_executor=adapter_executor,
    )

    # Then: the request policy controls hash verification for every runner
    # call - one for the object crop, one for its tile.
    assert exit_code == 0
    assert len(cache_policies) == 2
    assert all(
        policy.root == stage_request.model_cache_root for policy in cache_policies
    )
    assert not any(policy.verify_hashes for policy in cache_policies)


def test_startup_runner_fails_when_preprocessing_manifest_is_missing(
    tmp_path: Path,
) -> None:
    # Given: no preprocessing manifest exists for the project.
    stage_request = make_request(tmp_path, dry_run=True)

    # When: rough masking uses the public startup stage runner.
    exit_code = run_rough_masking_stage(stage_request)

    # Then: orchestration receives a failure exit code instead of a silent skip.
    assert exit_code == 2


def test_startup_runner_fails_real_run_with_no_accepted_candidates(
    tmp_path: Path,
) -> None:
    # Given: model-backed lanes execute but normalize no accepted candidates.
    stage_request = make_request(tmp_path)
    _ = write_real_manifest_assets(stage_request)
    write_model_inventory(stage_request)

    def runner_factory(
        *,
        lane: DetectorLane,
        image_path: Path,
        model_entries: dict[str, ModelInventoryEntry],
        device: str,
        model_cache_policy: object | None = None,
    ) -> DetectorRunner:
        _ = (lane, image_path, model_entries, device, model_cache_policy)

        def runner(request: AdapterRequest) -> RunnerOutcome:
            _ = request
            return RunnerOutcome(runner_invoked=True)

        return runner

    def adapter_executor(
        request: AdapterRequest, runner: DetectorRunner
    ) -> AdapterReceipt:
        _ = (request, runner)
        return AdapterReceipt(
            "owlv2_sam2",
            LaneExecutionStatus.REAL_EXECUTED,
            (),
            (),
            NoFakeClaimAudit(
                active_lane_only=True,
                fabricated_candidate_count=0,
                runner_invoked=True,
            ),
        )

    # When: every lane returns zero accepted rough candidates.
    exit_code = run_rough_masking_stage(
        stage_request,
        runner_factory=runner_factory,
        adapter_executor=adapter_executor,
    )

    # Then: the project stage fails closed.
    assert exit_code == 2
