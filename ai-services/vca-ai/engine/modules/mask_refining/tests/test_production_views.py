from __future__ import annotations

import importlib
import sys
from dataclasses import replace
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest
from PIL import Image

import modules
from modules.mask_refining import (
    EvidenceFailureCode,
    PillowQwenViewRenderer,
    QwenInputView,
    QwenRefinementRequest,
    QwenViewKind,
    refine_candidate,
)
from modules.mask_refining.contracts.models import MAX_AREA_PX
from modules.mask_refining.rendering.views import (
    renderer_is_independent,
    view_contract_failure,
)
from modules.mask_refining.tests.test_support import (
    IndependentBackend,
    make_candidate,
    valid_observation_json,
)
from modules.rough_masking import AssetReference
from modules.shared import ContractValidationError

if TYPE_CHECKING:
    from pathlib import Path


def _real_candidate(
    root: Path,
) -> tuple[QwenRefinementRequest, PillowQwenViewRenderer]:
    candidate, _ = make_candidate(root)
    source_path = root / "source.jpg"
    Image.new("RGB", (160, 120), (32, 64, 96)).save(source_path, format="JPEG")
    source_bytes = source_path.read_bytes()
    source = AssetReference(
        "source.jpg",
        sha256(source_bytes).hexdigest(),
        "image/jpeg",
    )
    mask_path = root / "masks" / "candidate.png"
    mask = Image.new("L", (160, 120), 0)
    mask.paste(255, (16, 16, 144, 104))
    mask.save(mask_path, format="PNG")
    mask_bytes = mask_path.read_bytes()
    bounded_candidate = replace(
        candidate,
        bbox_xyxy=(8.0, 8.0, 152.0, 112.0),
        rough_mask=AssetReference(
            "masks/candidate.png",
            sha256(mask_bytes).hexdigest(),
            "image/png",
        ),
    )
    request = QwenRefinementRequest(
        bounded_candidate,
        source,
        root,
        "cpu",
        160,
        120,
    )
    return request, PillowQwenViewRenderer(root)


def _image_format(path: Path) -> str | None:
    with Image.open(path) as image:
        return image.format


def test_qwen_input_view_accepts_large_source_crop_with_budgeted_rendered_area() -> (
    None
):
    # Given: source-space crop metadata larger than the Qwen output-area limit.
    crop_xyxy = (0.0, 0.0, 160.0, 120.0)

    # When: the renderer records a 32-by-32 materialized view.
    view = QwenInputView(
        view_id="candidate-001:masked",
        kind=QwenViewKind.MASKED_TARGET_CROP,
        asset_hash="a" * 64,
        media_type="image/png",
        relative_path="views/masked.png",
        call_order=1,
        crop_xyxy=crop_xyxy,
        rendered_width_px=32,
        rendered_height_px=32,
    )

    # Then: source crop geometry remains provenance rather than an output rejection.
    assert view.crop_xyxy == crop_xyxy


def test_qwen_input_view_rejects_rendered_area_above_budget() -> None:
    # Given: a view whose materialized PNG dimensions exceed the locked budget.
    # When: the view contract is constructed.
    with pytest.raises(ContractValidationError) as error:
        _ = QwenInputView(
            view_id="candidate-001:masked",
            kind=QwenViewKind.MASKED_TARGET_CROP,
            asset_hash="a" * 64,
            media_type="image/png",
            relative_path="views/masked.png",
            call_order=1,
            rendered_width_px=33,
            rendered_height_px=32,
        )

    # Then: the rendered output budget is enforced at the contract boundary.
    assert error.value.field == "rendered_area_px"


def test_pillow_renderer_materializes_budgeted_deterministic_independent_png_views(
    tmp_path: Path,
) -> None:
    # Given: real source and rough-mask PNG assets with a large valid source crop.
    request, renderer = _real_candidate(tmp_path)

    # When: the production renderer materializes the required Qwen views twice.
    first_views = renderer.render(request.to_view_request())
    second_views = renderer.render(request.to_view_request())

    # Then: real deterministic PNGs satisfy the bounded independent-view contract.
    assert renderer_is_independent(renderer) is True
    assert view_contract_failure(first_views, tmp_path) is None
    assert tuple(view.asset_hash for view in first_views) == tuple(
        view.asset_hash for view in second_views
    )
    assert all(
        view.rendered_width_px * view.rendered_height_px <= MAX_AREA_PX
        for view in first_views
    )
    assert all(
        _image_format(tmp_path / view.relative_path) == "PNG" for view in first_views
    )


def test_production_renderer_allows_large_source_crop_after_rendering(
    tmp_path: Path,
) -> None:
    # Given: a valid source-space crop whose padded area is larger than MAX_AREA_PX.
    request, renderer = _real_candidate(tmp_path)

    # When: refinement invokes the production renderer and a valid Qwen backend.
    result = refine_candidate(
        request,
        renderer,
        IndependentBackend(valid_observation_json()),
    )

    # Then: budgeted rendered views permit the valid candidate to reach Qwen parsing.
    assert result.final_success is True
    assert result.input_views[0].crop_xyxy[2] * result.input_views[0].crop_xyxy[3] > (
        MAX_AREA_PX
    )


def test_refine_candidate_returns_failed_evidence_when_hash_matching_source_is_corrupt(
    tmp_path: Path,
) -> None:
    # Given: a source asset whose recorded hash matches corrupt image bytes.
    request, renderer = _real_candidate(tmp_path)
    source_path = tmp_path / request.source_asset.relative_path
    corrupt_source = b"not-a-decodable-jpeg"
    _ = source_path.write_bytes(corrupt_source)
    corrupt_request = replace(
        request,
        source_asset=replace(
            request.source_asset,
            sha256=sha256(corrupt_source).hexdigest(),
        ),
    )

    # When: the production renderer decodes the hash-valid source asset.
    result = refine_candidate(
        corrupt_request,
        renderer,
        IndependentBackend(valid_observation_json()),
    )

    # Then: decode failure remains an explicit non-null Qwen evidence record.
    assert result.final_success is False
    assert result.failure_code is EvidenceFailureCode.IMAGE_DECODE_FAILED


def test_refine_candidate_returns_failed_evidence_when_png_header_valid_mask_is_corrupt(
    tmp_path: Path,
) -> None:
    # Given: a mask with a valid PNG signature but corrupt hash-matching content.
    request, renderer = _real_candidate(tmp_path)
    mask_path = tmp_path / request.candidate.rough_mask.relative_path
    corrupt_mask = b"\x89PNG\r\n\x1a\ncorrupt-mask-content"
    _ = mask_path.write_bytes(corrupt_mask)
    corrupt_request = replace(
        request,
        candidate=replace(
            request.candidate,
            rough_mask=replace(
                request.candidate.rough_mask,
                sha256=sha256(corrupt_mask).hexdigest(),
            ),
        ),
    )

    # When: the production renderer decodes the signature-valid rough mask.
    result = refine_candidate(
        corrupt_request,
        renderer,
        IndependentBackend(valid_observation_json()),
    )

    # Then: corrupt decoder data cannot escape or become successful evidence.
    assert result.final_success is False
    assert result.failure_code is EvidenceFailureCode.IMAGE_DECODE_FAILED


def test_contract_import_does_not_load_optional_production_renderer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a process that previously loaded the package and production renderer.
    package_name = "modules.mask_refining"
    renderer_module_name = f"{package_name}.production_views"
    monkeypatch.delitem(sys.modules, package_name, raising=False)
    monkeypatch.delitem(sys.modules, renderer_module_name, raising=False)
    monkeypatch.delattr(modules, "mask_refining", raising=False)

    # When: ordinary package contracts are imported without requesting the renderer.
    package = importlib.import_module(package_name)

    # Then: the optional Pillow renderer module remains unloaded.
    assert package.__name__ == package_name
    assert renderer_module_name not in sys.modules
