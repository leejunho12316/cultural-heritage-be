from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from modules.preprocessing import ScaleConfidence, ScaleMetadata, ViewKind, ViewRecord
from modules.prompt_generating import (
    PromptPack,
    static_fallback_ablation_pack,
    static_seed_minimal_pack,
)
from modules.rough_masking import (
    AdapterRequest,
    ImageDimensions,
    RunnerOutcome,
    SeedRequestPaths,
    build_seed_request,
    execute_adapter,
    seed_thresholds,
)
from modules.rough_masking.contracts import SeedThresholds
from modules.shared import ContractValidationError, DetectorLane, ImageId, PromptRole

if TYPE_CHECKING:
    from pathlib import Path


type RecordAtom = str | int | float | bool | None
type RecordValue = RecordAtom | list[RecordAtom]
type Record = dict[str, RecordValue]


def make_view() -> ViewRecord:
    return ViewRecord(
        view_id="view-full-001",
        kind=ViewKind.FULL_IMAGE,
        image_id=ImageId("image-001"),
        object_id=None,
        tile_view_id=None,
        source_view_id=None,
        rag_followup_view_id=None,
        view_reuse_mode=None,
        coordinate_transform=None,
        scale_metadata=ScaleMetadata(
            scale_marker_detected=False,
            scale_marker_bbox=None,
            scale_marker_width_px=None,
            scale_unit_px=None,
            scale_unit_source=None,
            scale_confidence=ScaleConfidence.UNAVAILABLE,
            confidence_reasons=("fixture",),
            fallback_reason="unavailable",
        ),
    )


def make_request(tmp_path: Path, lane: DetectorLane) -> AdapterRequest:
    return build_seed_request(
        lane=lane,
        view=make_view(),
        image_dimensions=ImageDimensions(100, 80),
        paths=SeedRequestPaths(
            lane_output_dir=tmp_path / lane.value,
            records_json=tmp_path / lane.value / "records.json",
        ),
        prompt_pack=static_seed_minimal_pack,
    )


def make_record(request: AdapterRequest) -> Record:
    return {
        "accepted": True,
        "image": request.view.image_id,
        "prompt": request.prompts[0].prompt_text,
        "score": 0.7,
        "bbox_xyxy": [1, 2, 20, 30],
        "mask_path": "mask.png",
        "mask_semantics": "anomaly_region",
        "overlay_path": "overlay.jpg",
        "prompt_pack_id": request.prompts[0].metadata.prompt_pack_id,
        "generation_lane": request.prompts[0].metadata.model_lane.value,
    }


def write_records(request: AdapterRequest, records: list[Record]) -> None:
    _ = request.records_json.write_text(json.dumps(records))


def invoked_runner(request: AdapterRequest) -> RunnerOutcome:
    _ = request
    return RunnerOutcome(runner_invoked=True)


def not_invoked_runner(request: AdapterRequest) -> RunnerOutcome:
    _ = request
    return RunnerOutcome(runner_invoked=False)


@pytest.mark.parametrize(
    "lane",
    [
        DetectorLane.OWLV2_SAM2,
        DetectorLane.FLORENCE2_SAM2,
        DetectorLane.GROUNDED_SAM2,
    ],
)
def test_adapter_normalizes_real_runner_records_for_every_active_lane(
    tmp_path: Path, lane: DetectorLane
) -> None:
    # Given: a runner-produced record and contained detector assets.
    request = make_request(tmp_path, lane)
    request.lane_output_dir.mkdir()
    _ = (request.lane_output_dir / "mask.png").write_bytes(b"mask")
    _ = (request.lane_output_dir / "overlay.jpg").write_bytes(b"overlay")
    write_records(request, [make_record(request)])

    # When: the mockable runner seam reports an invoked execution.
    receipt = execute_adapter(request, invoked_runner)

    # Then: exactly one validated, provenance-preserving candidate is emitted.
    assert receipt.status.value == "real_executed"
    assert receipt.no_fake_claim_audit.runner_invoked is True
    assert receipt.no_fake_claim_audit.fabricated_candidate_count == 0
    assert receipt.candidates[0].lane is lane
    assert receipt.candidates[0].prompt_provenance.prompt_role is PromptRole.STATIC_SEED
    assert receipt.candidates[0].rough_mask.sha256
    assert receipt.candidates[0].overlay.sha256


@pytest.mark.parametrize(
    ("mask_semantics", "diagnostic"),
    [
        ("object_roi", "mask_semantics_invalid"),
        (None, "mask_semantics_missing"),
    ],
)
def test_adapter_rejects_non_anomaly_mask_semantics(
    tmp_path: Path, mask_semantics: str | None, diagnostic: str
) -> None:
    # Given: a runner record whose mask is not proven to be an anomaly region.
    request = make_request(tmp_path, DetectorLane.OWLV2_SAM2)
    request.lane_output_dir.mkdir()
    _ = (request.lane_output_dir / "mask.png").write_bytes(b"mask")
    _ = (request.lane_output_dir / "overlay.jpg").write_bytes(b"overlay")
    record = make_record(request)
    if mask_semantics is None:
        _ = record.pop("mask_semantics")
    else:
        record["mask_semantics"] = mask_semantics
    write_records(request, [record])

    # When: the adapter normalizes the invoked runner output.
    receipt = execute_adapter(request, invoked_runner)

    # Then: object ROI masks cannot become rough-mask candidates.
    assert receipt.candidates == ()
    assert receipt.diagnostics == (diagnostic,)


@pytest.mark.parametrize(
    ("lane", "expected"),
    [
        (DetectorLane.OWLV2_SAM2, SeedThresholds(0.08, None, 2, 0.30)),
        (DetectorLane.GROUNDED_SAM2, SeedThresholds(0.25, 0.25, 2, 0.35)),
        (DetectorLane.FLORENCE2_SAM2, SeedThresholds(None, None, 1, 0.40)),
    ],
)
def test_seed_thresholds_are_locked_for_every_active_lane(
    lane: DetectorLane, expected: SeedThresholds
) -> None:
    # Given: an active lane.
    # When: its threshold family is resolved.
    # Then: every field equals the locked detector threshold family.
    assert seed_thresholds(lane) == expected


def test_contracts_fail_closed_for_clipseg_fake_success_and_seed_drift(
    tmp_path: Path,
) -> None:
    # Given: excluded-lane, fake-success, and altered seed-pack inputs.
    altered = replace(
        static_seed_minimal_pack.records[0], prompt_text="boundary shadow"
    )
    drifted = PromptPack(
        prompt_pack_id=static_seed_minimal_pack.prompt_pack_id,
        prompt_role=PromptRole.STATIC_SEED,
        records=(altered, *static_seed_minimal_pack.records[1:]),
    )

    # When: T4 validates every boundary.
    # Then: none can become active execution.
    with pytest.raises(ContractValidationError):
        _ = make_request(tmp_path, DetectorLane.CLIPSEG)
    with pytest.raises(ContractValidationError):
        _ = build_seed_request(
            lane=DetectorLane.OWLV2_SAM2,
            view=make_view(),
            image_dimensions=ImageDimensions(100, 80),
            paths=SeedRequestPaths(
                lane_output_dir=tmp_path,
                records_json=tmp_path / "x",
            ),
            prompt_pack=drifted,
        )
    with pytest.raises(ContractValidationError):
        _ = build_seed_request(
            lane=DetectorLane.OWLV2_SAM2,
            view=make_view(),
            image_dimensions=ImageDimensions(100, 80),
            paths=SeedRequestPaths(
                lane_output_dir=tmp_path,
                records_json=tmp_path / "x",
            ),
            prompt_pack=static_fallback_ablation_pack,
        )
    with pytest.raises(ContractValidationError):
        _ = execute_adapter(
            make_request(tmp_path, DetectorLane.OWLV2_SAM2),
            not_invoked_runner,
        )


def test_request_rejects_threshold_family_mixup_and_missing_prompts(
    tmp_path: Path,
) -> None:
    # Given: an OWLv2 request modified with an unrelated threshold family or no prompts.
    request = make_request(tmp_path, DetectorLane.OWLV2_SAM2)

    # When: AdapterRequest validates the changed values.
    # Then: neither invalid request can be constructed.
    with pytest.raises(ContractValidationError, match="threshold_config"):
        _ = replace(
            request,
            threshold_config=seed_thresholds(DetectorLane.FLORENCE2_SAM2),
        )
    with pytest.raises(ContractValidationError, match="seed_prompts"):
        _ = replace(request, prompts=())


def test_request_rejects_invalid_image_dimensions(tmp_path: Path) -> None:
    # Given: an active request with a non-positive image width.
    # When: the request is constructed at the contract boundary.
    # Then: it returns the documented image-dimension diagnostic.
    with pytest.raises(ContractValidationError) as raised:
        _ = build_seed_request(
            lane=DetectorLane.OWLV2_SAM2,
            view=make_view(),
            image_dimensions=ImageDimensions(0, 80),
            paths=SeedRequestPaths(
                lane_output_dir=tmp_path,
                records_json=tmp_path / "records.json",
            ),
            prompt_pack=static_seed_minimal_pack,
        )
    assert raised.value.field == "image_dimensions_invalid"
