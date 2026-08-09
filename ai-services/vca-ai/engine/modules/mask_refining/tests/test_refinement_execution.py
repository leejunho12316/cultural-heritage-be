from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from modules.mask_refining.execution import run_refinement
from modules.mask_refining.execution import runner as execution
from modules.mask_refining.execution.assets import join_preprocessing_assets
from modules.mask_refining.execution.models import (
    PostRefinementQwenEvidence,
    RefinementRunRequest,
    RunnerFactoryInput,
)
from modules.mask_refining.execution.prompts import read_prompt_variants
from modules.rag.operations.candidate_sidecar_artifacts import RoughRagCandidate
from modules.rag.qwen.qwen_bridge_json import parse_json_object
from modules.rough_masking import (
    AnomalyMaskOutput,
    DetectorRunner,
    RunnerOutcome,
    materialize_anomaly_outputs,
)
from modules.rough_masking.artifacts.assets import AssetReference
from modules.rough_masking.candidates import CandidateStatus, RawDetectorCandidate
from modules.rough_masking.contracts import AdapterRequest, SeedThresholds
from modules.shared import (
    CandidateId,
    ContractValidationError,
    DetectorLane,
    ImageId,
    PromptMetadata,
    PromptRole,
    RagLane,
)
from modules.visual_cue_generation.rough_records import RoughQwenCandidate

if TYPE_CHECKING:
    from pathlib import Path

PNG_HEADER = b"\x89PNG\r\n\x1a\n"
JPEG_HEADER = b"\xff\xd8"
IMPORT_POLICY_SCRIPT = (
    "import sys; import modules.mask_refining; "
    "print(','.join(sorted(set(sys.modules) & "
    "{'PIL','torch','transformers','sam2'})))"
)
CLI_IMPORT_POLICY_SCRIPT = (
    "import sys; import modules.mask_refining.refinement_cli; "
    "print(','.join(sorted(set(sys.modules) & "
    "{'PIL','torch','transformers','sam2'})))"
)


def _write_prompt_inputs(root: Path) -> None:
    root.mkdir()
    _ = (root / "manifest.json").write_text(
        json.dumps({"schema": "rag_refinement_prompt_variants_smoke_v1"})
    )
    valid = {
        "concept_card_id": "card-001",
        "generated_prompt": "surface crack",
        "generated_prompt_id": "prompt-001",
        "model_lane": "owlv2",
        "model_prompt_variant": "owlv2",
        "prompt_pack_id": "rag-refinement-v1",
        "prompt_role": "rag_refinement",
        "rag_parent_candidate_id": "rough-parent-001",
        "source_citation_ids": ["citation-001"],
        "source_concept_family": "crack",
        "source_terms": ["crack", "surface"],
    }
    _ = (root / "rag_refinement_prompt_variants.jsonl").write_text(
        f"{json.dumps(valid)}\nnot-json\n"
    )


def _write_preprocessing_assets(root: Path) -> None:
    object_root = root / "assets" / "objects" / "object-001"
    object_root.mkdir(parents=True)
    _ = (object_root / "bbox_crop.jpg").write_bytes(
        PNG_HEADER + b"\x00\x00\x00\rIHDR\x00\x00\x00\x20\x00\x00\x00\x10"
    )
    _ = (object_root / "mask.png").write_bytes(PNG_HEADER + b"mask")
    manifest = {
        "objects": [
            {
                "object_id": "object-001",
                "image_id": "image-001",
                "bbox_xyxy": [10.0, 20.0, 42.0, 36.0],
                "bbox_crop": {"path": str(object_root / "bbox_crop.jpg")},
                "mask": {"path": str(object_root / "mask.png")},
            }
        ]
    }
    manifests = root / "manifests"
    manifests.mkdir()
    _ = (manifests / "real_preprocessing_manifest.json").write_text(
        json.dumps(manifest)
    )


def _rough_candidate() -> RoughQwenCandidate:
    metadata = PromptMetadata(
        "static-seed", PromptRole.STATIC_SEED, RagLane.OWLV2, "seed-001", ("mark",)
    )
    candidate = RawDetectorCandidate(
        CandidateId("rough-parent-001"),
        ImageId("image-001"),
        DetectorLane.OWLV2_SAM2,
        CandidateStatus.ACCEPTED,
        "mark",
        0.9,
        (1.0, 1.0, 2.0, 2.0),
        AssetReference("mask.png", "a" * 64, "image/png"),
        AssetReference("overlay.jpg", "b" * 64, "image/jpeg"),
        "detector",
        "sam2",
        SeedThresholds(0.08, None, 2, 0.3),
        metadata,
        "source-001",
        "object-001",
        None,
        (),
    )
    return RoughQwenCandidate(
        rough=RoughRagCandidate(
            "owlv2_sam2",
            "mark",
            "owlv2_sam2/object-001/records.json",
            0,
            CandidateId("rough-parent-001"),
            "image-001",
        ),
        candidate=candidate,
    )


def _fake_qwen_evidence_factory(
    candidate: RawDetectorCandidate,
    source_asset: AssetReference | None,
    assets: object,
    lane_output_dir: Path,
) -> PostRefinementQwenEvidence:
    # Given: a stand-in that never loads the real Qwen model in unit tests.
    _ = (candidate, source_asset, assets, lane_output_dir)
    return PostRefinementQwenEvidence(
        final_success=True, report_display_text="테스트 관찰 문구", confidence=0.9
    )


def test_prompt_variant_reader_groups_valid_rows_and_skips_malformed_rows(
    tmp_path: Path,
) -> None:
    # Given: one valid RAG variant and a malformed JSONL row.
    prompt_root = tmp_path / "prompts"
    _write_prompt_inputs(prompt_root)

    # When: the artifact reader parses the directory.
    result = read_prompt_variants(prompt_root)

    # Then: it retains the typed variant group and reports the bad line.
    assert len(result.groups) == 1
    assert result.groups[0].variants[0].generated_prompt == "surface crack"
    assert result.groups[0].model_lane is RagLane.OWLV2
    assert result.skips[0].line_number == 2


def test_refinement_execution_materializes_rag_prompt_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: prompt and object assets plus a fake runner materialization seam.
    prompt_root = tmp_path / "prompts"
    asset_root = tmp_path / "assets-root"
    output_root = tmp_path / "output"
    _write_prompt_inputs(prompt_root)
    _write_preprocessing_assets(asset_root)

    def rough_candidates(_rough_root: Path) -> tuple[RoughQwenCandidate, ...]:
        return (_rough_candidate(),)

    monkeypatch.setattr(execution, "_rough_qwen_candidates", rough_candidates)
    captured_prompts: list[str] = []

    def runner_factory(details: RunnerFactoryInput) -> DetectorRunner:
        def runner(request: AdapterRequest) -> RunnerOutcome:
            captured_prompts.extend(prompt.prompt_text for prompt in request.prompts)
            output = AnomalyMaskOutput(
                prompt=request.prompts[0],
                score=0.8,
                bbox_xyxy=(1.0, 1.0, 8.0, 8.0),
                mask_png=PNG_HEADER + b"mask",
                overlay_jpeg=JPEG_HEADER + b"overlay",
                quality_filter_version="test",
                quality_score=0.8,
                mask_area_ratio=0.1,
                bbox_fill_ratio=0.5,
                boundary_pixel_ratio=0.0,
                perimeter_coverage_ratio=0.0,
                border_touch_count=0,
                component_count=1,
                largest_component_ratio=1.0,
            )
            materialize_anomaly_outputs(request, (output,))
            return RunnerOutcome(runner_invoked=True)

        _ = details
        return runner

    request = RefinementRunRequest(
        prompt_output_dir=prompt_root,
        rough_root=tmp_path / "rough",
        asset_root=asset_root,
        output_dir=output_root,
        model_cache_root=tmp_path / "models",
        device="cpu",
        verify_model_hashes=False,
        max_groups=None,
    )

    # When: RAG refinement is executed through the fake runner.
    result = run_refinement(request, runner_factory, _fake_qwen_evidence_factory)

    # Then: the RAG text reaches AdapterRequest and all public artifacts are written.
    assert captured_prompts == ["surface crack"]
    assert result.executed_groups == 1
    assert (output_root / "refined_records.jsonl").is_file()
    assert (output_root / "skips.jsonl").is_file()
    assert (output_root / "manifest.json").is_file()
    record = parse_json_object(
        (output_root / "refined_records.jsonl").read_text(encoding="utf-8")
    )
    assert record["rag_parent_candidate_id"] == "rough-parent-001"
    assert record["prompt_texts"] == ["surface crack"]
    normalized_candidate_id = result.records[0].accepted_candidate_ids[0]
    assert record["accepted_candidates"] == [
        {
            "bbox_xyxy": [1.0, 1.0, 8.0, 8.0],
            "original_bbox_xyxy": [11.0, 21.0, 18.0, 28.0],
            "candidate_id": normalized_candidate_id,
            "image_id": "image-001",
            "prompt": "surface crack",
            "source_object_id": "object-001",
            "source_view_id": "refinement-object:object-001",
            "qwen_final_success": True,
            "qwen_report_display_text": "테스트 관찰 문구",
            "qwen_confidence": 0.9,
            # No input_manifest.json in this fixture, so the original image
            # size is unknown and the crop-local mask cannot be safely
            # restored to source-image coordinates (see
            # _restore_original_mask) - anomaly_grouping falls back to the
            # rough_masking mask in this case.
            "mask_path": None,
            "mask_sha256": None,
        }
    ]
    manifest = parse_json_object(
        (output_root / "manifest.json").read_text(encoding="utf-8")
    )
    counts = manifest["counts"]
    assert isinstance(counts, dict)
    assert counts["executed_groups"] == 1
    inputs = manifest["inputs"]
    assert isinstance(inputs, dict)
    assert inputs["input_roles"] == {
        "prompt_output_dir": "prompt_generating output",
        "rough_root": "rough_masking output",
        "asset_root": "preprocessing assets",
    }


def test_refinement_execution_passes_through_candidates_without_rag_prompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a rough candidate but zero RAG-derived prompt groups for it (RAG
    # found no citable evidence this run) plus real preprocessing assets.
    prompt_root = tmp_path / "prompts"
    asset_root = tmp_path / "assets-root"
    output_root = tmp_path / "output"
    prompt_root.mkdir()
    _ = (prompt_root / "manifest.json").write_text(
        json.dumps({"schema": "rag_refinement_prompt_variants_smoke_v1"})
    )
    _ = (prompt_root / "rag_refinement_prompt_variants.jsonl").write_text("")
    _write_preprocessing_assets(asset_root)

    def rough_candidates(_rough_root: Path) -> tuple[RoughQwenCandidate, ...]:
        return (_rough_candidate(),)

    monkeypatch.setattr(execution, "_rough_qwen_candidates", rough_candidates)

    request = RefinementRunRequest(
        prompt_output_dir=prompt_root,
        rough_root=tmp_path / "rough",
        asset_root=asset_root,
        output_dir=output_root,
        model_cache_root=tmp_path / "models",
        device="cpu",
        verify_model_hashes=False,
        max_groups=None,
    )

    # When: refinement runs with no prompt groups to execute at all (default
    # runner_factory/qwen_evidence_factory are never invoked with zero groups).
    result = run_refinement(request)

    # Then: no real refinement group ran, but the candidate still shows up in
    # a passthrough sidecar - RAG evidence is missing, not the candidate.
    assert result.records == ()
    passthrough_path = output_root / "passthrough_records.jsonl"
    assert passthrough_path.is_file()
    row = parse_json_object(passthrough_path.read_text(encoding="utf-8").strip())
    assert row["rag_parent_candidate_id"] == "rough-parent-001"
    accepted = row["accepted_candidates"]
    assert isinstance(accepted, list)
    assert accepted[0]["candidate_id"] == "rough-parent-001"
    assert accepted[0]["qwen_final_success"] is False
    assert accepted[0]["qwen_report_display_text"] == "없음"
    # No input_manifest.json in this fixture, so mask restoration can't
    # determine original-image size - same documented fallback boundary as
    # the real-refinement path above.
    assert accepted[0]["mask_path"] is None


def test_passthrough_skips_one_candidate_instead_of_crashing_the_whole_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the same "zero RAG evidence" passthrough trigger as the happy
    # path above, but combined with an original image too small for the
    # candidate's restored bbox to fit inside (same setup that makes the
    # real-refinement path above fail closed for one group).
    prompt_root = tmp_path / "prompts"
    asset_root = tmp_path / "assets-root"
    output_root = tmp_path / "output"
    prompt_root.mkdir()
    _ = (prompt_root / "manifest.json").write_text(
        json.dumps({"schema": "rag_refinement_prompt_variants_smoke_v1"})
    )
    _ = (prompt_root / "rag_refinement_prompt_variants.jsonl").write_text("")
    _write_preprocessing_assets(asset_root)
    original_path = asset_root / "assets" / "raw-inputs" / "image-001.jpg"
    original_path.parent.mkdir(parents=True)
    Image.new("RGB", (15, 15), (0, 0, 0)).save(original_path, format="JPEG")
    _ = (asset_root / "manifests" / "input_manifest.json").write_text(
        json.dumps(
            {
                "images": [
                    {
                        "image_id": "image-001",
                        "run_root_asset_path": str(original_path),
                    }
                ]
            }
        )
    )

    def rough_candidates(_rough_root: Path) -> tuple[RoughQwenCandidate, ...]:
        return (_rough_candidate(),)

    monkeypatch.setattr(execution, "_rough_qwen_candidates", rough_candidates)

    request = RefinementRunRequest(
        prompt_output_dir=prompt_root,
        rough_root=tmp_path / "rough",
        asset_root=asset_root,
        output_dir=output_root,
        model_cache_root=tmp_path / "models",
        device="cpu",
        verify_model_hashes=False,
        max_groups=None,
    )

    # When: refinement runs with zero prompt groups and a candidate whose
    # coordinate restoration will fail.
    result = run_refinement(request)

    # Then: the stage does not crash - the candidate is skipped, not
    # silently fabricated and not fatal to the whole run.
    assert result.records == ()
    assert len(result.skips) == 1
    assert result.skips[0].rag_parent_candidate_id == "rough-parent-001"
    assert "passthrough_coordinate_restoration_failed" in result.skips[0].reason
    passthrough_path = output_root / "passthrough_records.jsonl"
    assert passthrough_path.is_file()
    assert passthrough_path.read_text(encoding="utf-8") == ""


def test_refinement_execution_fails_group_when_original_bbox_exceeds_image_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the same fixtures as the happy path, but the original image is
    # declared far smaller than the restored bbox could possibly fit inside.
    prompt_root = tmp_path / "prompts"
    asset_root = tmp_path / "assets-root"
    output_root = tmp_path / "output"
    _write_prompt_inputs(prompt_root)
    _write_preprocessing_assets(asset_root)
    original_path = asset_root / "assets" / "raw-inputs" / "image-001.jpg"
    original_path.parent.mkdir(parents=True)
    Image.new("RGB", (15, 15), (0, 0, 0)).save(original_path, format="JPEG")
    _ = (asset_root / "manifests" / "input_manifest.json").write_text(
        json.dumps(
            {
                "images": [
                    {
                        "image_id": "image-001",
                        "run_root_asset_path": str(original_path),
                    }
                ]
            }
        )
    )

    def rough_candidates(_rough_root: Path) -> tuple[RoughQwenCandidate, ...]:
        return (_rough_candidate(),)

    monkeypatch.setattr(execution, "_rough_qwen_candidates", rough_candidates)

    def runner_factory(details: RunnerFactoryInput) -> DetectorRunner:
        def runner(request: AdapterRequest) -> RunnerOutcome:
            output = AnomalyMaskOutput(
                prompt=request.prompts[0],
                score=0.8,
                bbox_xyxy=(1.0, 1.0, 8.0, 8.0),
                mask_png=PNG_HEADER + b"mask",
                overlay_jpeg=JPEG_HEADER + b"overlay",
                quality_filter_version="test",
                quality_score=0.8,
                mask_area_ratio=0.1,
                bbox_fill_ratio=0.5,
                boundary_pixel_ratio=0.0,
                perimeter_coverage_ratio=0.0,
                border_touch_count=0,
                component_count=1,
                largest_component_ratio=1.0,
            )
            materialize_anomaly_outputs(request, (output,))
            return RunnerOutcome(runner_invoked=True)

        _ = details
        return runner

    request = RefinementRunRequest(
        prompt_output_dir=prompt_root,
        rough_root=tmp_path / "rough",
        asset_root=asset_root,
        output_dir=output_root,
        model_cache_root=tmp_path / "models",
        device="cpu",
        verify_model_hashes=False,
        max_groups=None,
    )

    # When: RAG refinement is executed through the fake runner.
    result = run_refinement(request, runner_factory, _fake_qwen_evidence_factory)

    # Then: the group fails closed instead of publishing an out-of-bounds box.
    assert result.executed_groups == 0
    assert len(result.records) == 1
    assert result.records[0].status.value == "failed"
    assert any("bbox" in diagnostic for diagnostic in result.records[0].diagnostics)


def test_preprocessing_join_reads_actual_jpeg_crop_dimensions(tmp_path: Path) -> None:
    # Given: a fractional preprocessing bbox whose rounded JPEG crop is 19 px wide.
    root = tmp_path / "assets-root"
    object_root = root / "assets" / "objects" / "object-001"
    object_root.mkdir(parents=True)
    crop_path = object_root / "bbox_crop.jpg"
    Image.new("RGB", (19, 11), (255, 255, 255)).save(crop_path, format="JPEG")
    mask_path = object_root / "mask.png"
    _ = mask_path.write_bytes(PNG_HEADER + b"mask")
    manifest = {
        "objects": [
            {
                "object_id": "object-001",
                "image_id": "image-001",
                "bbox_xyxy": [10.9, 5.2, 30.1, 16.4],
                "bbox_crop": {"path": str(crop_path)},
                "mask": {"path": str(mask_path)},
            }
        ]
    }
    manifests = root / "manifests"
    manifests.mkdir()
    _ = (manifests / "real_preprocessing_manifest.json").write_text(
        json.dumps(manifest)
    )

    # When: refinement joins preprocessing ROI assets.
    assets = join_preprocessing_assets(root, "object-001", "image-001")

    # Then: dimensions and transform match the actual rounded crop, not ceil bbox.
    assert assets is not None
    assert (assets.image_width_px, assets.image_height_px) == (19, 11)
    assert assets.view.coordinate_transform is not None
    assert assets.view.coordinate_transform.restore_offset_x == 11.0
    assert assets.view.coordinate_transform.source_bbox.width == 19.0


def test_preprocessing_join_reads_original_image_dimensions(tmp_path: Path) -> None:
    # Given: an input_manifest.json recording the original full-resolution image.
    root = tmp_path / "assets-root"
    _write_preprocessing_assets(root)
    original_path = root / "assets" / "raw-inputs" / "image-001.jpg"
    original_path.parent.mkdir(parents=True)
    Image.new("RGB", (200, 150), (0, 0, 0)).save(original_path, format="JPEG")
    _ = (root / "manifests" / "input_manifest.json").write_text(
        json.dumps(
            {
                "images": [
                    {
                        "image_id": "image-001",
                        "run_root_asset_path": str(original_path),
                    }
                ]
            }
        )
    )

    # When: refinement joins preprocessing ROI assets.
    assets = join_preprocessing_assets(root, "object-001", "image-001")

    # Then: the original full-image pixel size is recovered from input_manifest.json.
    assert assets is not None
    assert (assets.original_image_width_px, assets.original_image_height_px) == (
        200,
        150,
    )


def test_preprocessing_join_reads_original_image_webp_dimensions(
    tmp_path: Path,
) -> None:
    # Given: an input_manifest.json pointing at a lossy (VP8) WEBP original -
    # Spring's upload validator accepts image/webp alongside jpeg/png.
    root = tmp_path / "assets-root"
    _write_preprocessing_assets(root)
    original_path = root / "assets" / "raw-inputs" / "image-001.webp"
    original_path.parent.mkdir(parents=True)
    Image.new("RGB", (200, 150), (0, 0, 0)).save(original_path, format="WEBP")
    _ = (root / "manifests" / "input_manifest.json").write_text(
        json.dumps(
            {
                "images": [
                    {
                        "image_id": "image-001",
                        "run_root_asset_path": str(original_path),
                    }
                ]
            }
        )
    )

    # When: refinement joins preprocessing ROI assets.
    assets = join_preprocessing_assets(root, "object-001", "image-001")

    # Then: the WEBP header is decoded instead of silently failing to restore.
    assert assets is not None
    assert (assets.original_image_width_px, assets.original_image_height_px) == (
        200,
        150,
    )


def test_preprocessing_join_reads_original_image_lossless_webp_dimensions(
    tmp_path: Path,
) -> None:
    # Given: a lossless (VP8L) WEBP original, a different sub-format from the
    # lossy VP8 case above with its own bit-packed header layout.
    root = tmp_path / "assets-root"
    _write_preprocessing_assets(root)
    original_path = root / "assets" / "raw-inputs" / "image-001.webp"
    original_path.parent.mkdir(parents=True)
    Image.new("RGB", (200, 150), (0, 0, 0)).save(
        original_path, format="WEBP", lossless=True
    )
    _ = (root / "manifests" / "input_manifest.json").write_text(
        json.dumps(
            {
                "images": [
                    {
                        "image_id": "image-001",
                        "run_root_asset_path": str(original_path),
                    }
                ]
            }
        )
    )

    # When: refinement joins preprocessing ROI assets.
    assets = join_preprocessing_assets(root, "object-001", "image-001")

    # Then: the VP8L header is decoded correctly too.
    assert assets is not None
    assert (assets.original_image_width_px, assets.original_image_height_px) == (
        200,
        150,
    )


def test_preprocessing_join_reads_original_image_tiff_dimensions(
    tmp_path: Path,
) -> None:
    # Given: an input_manifest.json pointing at a TIFF original - also
    # explicitly accepted by Spring's upload validator.
    root = tmp_path / "assets-root"
    _write_preprocessing_assets(root)
    original_path = root / "assets" / "raw-inputs" / "image-001.tiff"
    original_path.parent.mkdir(parents=True)
    Image.new("RGB", (200, 150), (0, 0, 0)).save(original_path, format="TIFF")
    _ = (root / "manifests" / "input_manifest.json").write_text(
        json.dumps(
            {
                "images": [
                    {
                        "image_id": "image-001",
                        "run_root_asset_path": str(original_path),
                    }
                ]
            }
        )
    )

    # When: refinement joins preprocessing ROI assets.
    assets = join_preprocessing_assets(root, "object-001", "image-001")

    # Then: the TIFF IFD is decoded instead of silently failing to restore.
    assert assets is not None
    assert (assets.original_image_width_px, assets.original_image_height_px) == (
        200,
        150,
    )


def test_preprocessing_join_original_dimensions_none_without_input_manifest(
    tmp_path: Path,
) -> None:
    # Given: preprocessing assets with no input_manifest.json (e.g. older runs).
    root = tmp_path / "assets-root"
    _write_preprocessing_assets(root)

    # When: refinement joins preprocessing ROI assets.
    assets = join_preprocessing_assets(root, "object-001", "image-001")

    # Then: original dimensions are unavailable rather than a hard failure.
    assert assets is not None
    assert assets.original_image_width_px is None
    assert assets.original_image_height_px is None


def test_refinement_rejects_symlink_output_root(tmp_path: Path) -> None:
    # Given: an output root symlink that could redirect refinement artifacts.
    prompt_root = tmp_path / "prompts"
    asset_root = tmp_path / "assets-root"
    output_target = tmp_path / "outside"
    output_target.mkdir()
    output_link = tmp_path / "output-link"
    output_link.symlink_to(output_target, target_is_directory=True)
    _write_prompt_inputs(prompt_root)
    _write_preprocessing_assets(asset_root)

    # When / Then: the run is rejected before writing through the symlink root.
    with pytest.raises(ContractValidationError, match="output_dir"):
        _ = run_refinement(
            RefinementRunRequest(
                prompt_output_dir=prompt_root,
                rough_root=tmp_path / "rough",
                asset_root=asset_root,
                output_dir=output_link,
                model_cache_root=tmp_path / "models",
                device="cpu",
                verify_model_hashes=False,
                max_groups=None,
            ),
            _unreachable_runner_factory,
        )


def _raise_unreachable_runner() -> DetectorRunner:
    reason = "runner factory must not be called"
    raise AssertionError(reason)


def _unreachable_runner_factory(details: RunnerFactoryInput) -> DetectorRunner:
    _ = details
    return _raise_unreachable_runner()


def test_package_import_does_not_load_model_or_image_runtimes() -> None:
    # Given: a fresh package import process.
    # When: its root package is imported.
    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-c",
            IMPORT_POLICY_SCRIPT,
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    # Then: no optional image/model runtime is loaded from that import surface.
    assert completed.stdout == "\n"


def test_cli_import_does_not_load_model_or_image_runtimes() -> None:
    # Given: a fresh CLI module import process.
    # When: the CLI module is imported without running refinement.
    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-c",
            CLI_IMPORT_POLICY_SCRIPT,
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    # Then: CLI import remains free of optional image/model runtimes.
    assert completed.stdout == "\n"
