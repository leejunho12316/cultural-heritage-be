from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

from PIL import Image

from modules.mask_refining.tests.test_support import (
    IndependentBackend,
    IndependentRenderer,
    valid_observation_json,
)
from modules.orchestration.stage_execution import ProjectStageRequest
from modules.orchestration.stage_paths import stage_paths
from modules.rag.qwen import read_qwen_bridge_results
from modules.shared import ExitCode, QwenBridgeStatus
from modules.visual_cue_generation.models import (
    QwenBridgeGenerationInputs,
    QwenBridgeGenerationResult,
)
from modules.visual_cue_generation.startup_runner import run_visual_cue_generation_stage

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from modules.mask_refining import QwenBackend
    from modules.mask_refining.rendering.views import QwenViewRenderer


@dataclass(frozen=True, slots=True)
class _CallLog:
    renderer_paths: list[Path]
    backend_paths: list[Path]
    generated_inputs: list[QwenBridgeGenerationInputs]


def _request(tmp_path: Path, *, dry_run: bool = False) -> ProjectStageRequest:
    return ProjectStageRequest(
        project_name="project-001",
        stage_name="visual_cue_generation",
        paths=stage_paths(tmp_path, "project-001"),
        device="mps",
        model_cache_root=tmp_path / "models",
        dry_run=dry_run,
        verify_model_hashes=True,
        output_root=tmp_path,
    )


def _write_startup_manifest(request: ProjectStageRequest, *, dry_run: bool) -> None:
    status = "dry_run_not_executed" if dry_run else "real_executed"
    manifest_dir = request.paths.preprocessing / "manifests"
    manifest_dir.mkdir(parents=True)
    _ = (manifest_dir / "real_preprocessing_manifest.json").write_text(
        (
            '{"schema_version":"vca-real-preprocessing-v2",'
            f'"detector_lane_status":"{status}",'
            '"model_invocations":0,"sam2_calls":0,'
            '"object_count":0,"objects":[],'
            '"tile_count":0,"requires_user_budget_approval":false}'
        ),
        encoding="utf-8",
    )


def _write_input_manifest(request: ProjectStageRequest) -> None:
    source_path = (
        request.paths.preprocessing / "assets" / "raw-inputs" / "image-001.jpg"
    )
    source_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), (255, 255, 255)).save(source_path, format="JPEG")
    source_bytes = source_path.read_bytes()
    manifest_path = request.paths.preprocessing / "manifests" / "input_manifest.json"
    _ = manifest_path.write_text(
        json.dumps(
            {
                "images": [
                    {
                        "file_sha256": sha256(source_bytes).hexdigest(),
                        "image_id": "image-001",
                        "mime_type": "image/jpeg",
                        "run_root_asset_path": str(source_path),
                    }
                ]
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _write_rough_record(request: ProjectStageRequest) -> None:
    lane_root = (
        request.paths.rough_masking
        / "owlv2_sam2"
        / "image-001-object-01"
        / "owlv2_sam2"
    )
    (lane_root / "masks").mkdir(parents=True, exist_ok=True)
    (lane_root / "overlays").mkdir(parents=True, exist_ok=True)
    Image.new("L", (64, 64), 255).save(
        lane_root / "masks" / "anomaly-0000.png",
        format="PNG",
    )
    _ = (lane_root / "overlays" / "anomaly-0000.jpg").write_bytes(b"\xff\xd8overlay")
    view_image_path = (
        request.paths.preprocessing / "assets" / "raw-inputs" / "image-001.jpg"
    )
    _ = (lane_root / "records.json").write_text(
        json.dumps(
            [
                {
                    "accepted": True,
                    "bbox_xyxy": [10.0, 12.0, 30.0, 32.0],
                    "view_origin_xyxy": [0.0, 0.0, 64.0, 64.0],
                    "view_image_path": str(view_image_path),
                    "generation_lane": "owlv2",
                    "image": "image-001",
                    "mask_path": "masks/anomaly-0000.png",
                    "mask_semantics": "anomaly_region",
                    "overlay_path": "overlays/anomaly-0000.jpg",
                    "prompt": "surface crack",
                    "prompt_pack_id": "static-seed-minimal-v1",
                    "score": 0.8,
                }
            ],
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def test_run_visual_cue_generation_stage_dry_run_skips_model_and_qwen_outputs(
    tmp_path: Path,
) -> None:
    # Given: preprocessing emitted a dry-run manifest and no Qwen artifacts exist.
    request = _request(tmp_path, dry_run=True)
    _write_startup_manifest(request, dry_run=True)

    # When: visual cue generation runs as a dry-run startup stage.
    exit_code = run_visual_cue_generation_stage(request)

    # Then: it validates the handoff without constructing fake Qwen outputs.
    assert exit_code == int(ExitCode.OK)
    assert not (request.paths.rag / "qwen_bridge_results").exists()
    assert not (request.paths.visual_cue_generation / "qwen_bridge_results").exists()


def test_run_visual_cue_generation_stage_can_skip_qwen_for_local_real_smoke(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a real startup request with local Qwen bridge generation disabled.
    request = _request(tmp_path)
    _write_startup_manifest(request, dry_run=False)
    monkeypatch.setenv("VCA_SKIP_VISUAL_CUES", "true")

    def fail_backend_loader(model_directory: Path, device: str) -> QwenBackend:
        _ = (model_directory, device)
        message = "Qwen backend must not load when visual cues are skipped."
        raise AssertionError(message)

    monkeypatch.setattr(
        "modules.visual_cue_generation.startup_runner.load_transformers_qwen_backend",
        fail_backend_loader,
    )

    # When: the visual cue stage runs in local smoke skip mode.
    exit_code = run_visual_cue_generation_stage(request)

    # Then: the stage succeeds without constructing Qwen artifacts.
    assert exit_code == int(ExitCode.OK)
    assert not (request.paths.rag / "qwen_bridge_results").exists()


def test_run_visual_cue_generation_stage_accepts_split_preprocessing_and_rough_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: startup outputs source assets and rough masks under sibling roots.
    request = _request(tmp_path)
    _write_startup_manifest(request, dry_run=False)
    _write_input_manifest(request)
    _write_rough_record(request)

    def backend_loader(model_directory: Path, device: str) -> IndependentBackend:
        _ = (model_directory, device)
        return IndependentBackend(valid_observation_json())

    monkeypatch.setattr(
        "modules.visual_cue_generation.startup_runner.load_transformers_qwen_backend",
        backend_loader,
    )

    # When: the visual cue stage reads both preprocessing and rough-mask artifacts.
    exit_code = run_visual_cue_generation_stage(request)

    # Then: split project roots are accepted and Qwen bridge rows are written.
    assert exit_code == int(ExitCode.OK)
    rows = tuple(
        read_qwen_bridge_results(request.paths.rag / "qwen_bridge_results").values()
    )
    assert len(rows) == 1
    assert rows[0].status is QwenBridgeStatus.SUCCESS


def test_run_visual_cue_generation_stage_calls_generator_with_project_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a real startup request and patched Qwen seams.
    request = _request(tmp_path)
    _write_startup_manifest(request, dry_run=False)
    shared_asset_root = request.paths.preprocessing.parent.parent
    calls = _CallLog([], [], [])

    def renderer_factory(asset_root: Path) -> QwenViewRenderer:
        calls.renderer_paths.append(asset_root)
        return IndependentRenderer(asset_root)

    def backend_loader(model_directory: Path, device: str) -> QwenBackend:
        _ = device
        calls.backend_paths.append(model_directory)
        return IndependentBackend(valid_observation_json())

    def generator(
        inputs: QwenBridgeGenerationInputs,
        renderer: QwenViewRenderer,
        backend: QwenBackend,
    ) -> QwenBridgeGenerationResult:
        _ = (renderer, backend)
        calls.generated_inputs.append(inputs)
        return QwenBridgeGenerationResult(
            request.paths.rag / "qwen_bridge_results", 0, 0, 0
        )

    monkeypatch.setattr(
        "modules.visual_cue_generation.startup_runner.PillowQwenViewRenderer",
        renderer_factory,
    )
    monkeypatch.setattr(
        "modules.visual_cue_generation.startup_runner.load_transformers_qwen_backend",
        backend_loader,
    )
    monkeypatch.setattr(
        "modules.visual_cue_generation.startup_runner.generate_qwen_bridge_results",
        generator,
    )

    # When: the real visual cue stage runner executes.
    exit_code = run_visual_cue_generation_stage(request)

    # Then: it passes the project-level artifact roots into generation exactly once.
    assert exit_code == int(ExitCode.OK)
    assert calls.renderer_paths == [shared_asset_root]
    assert calls.backend_paths == [
        request.model_cache_root / "hf" / "Qwen" / "Qwen2.5-VL-3B-Instruct"
    ]
    assert calls.generated_inputs == [
        QwenBridgeGenerationInputs(
            rough_root=request.paths.rough_masking,
            rag_run_dir=request.paths.rag,
            asset_root=shared_asset_root,
            device="mps",
        )
    ]
