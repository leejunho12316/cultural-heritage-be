from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from typing import TYPE_CHECKING, override

import pytest

from modules.mask_refining import QwenRefinementRequest, refine_candidate
from modules.mask_refining.tests.test_support import (
    IndependentBackend,
    IndependentRenderer,
    make_candidate,
    valid_observation_json,
)

if TYPE_CHECKING:
    from pathlib import Path

    from modules.mask_refining import QwenInputView
    from modules.mask_refining.contracts.models import ViewRenderRequest


class _MissingViewRenderer(IndependentRenderer):
    @override
    def render(self, request: ViewRenderRequest) -> tuple[QwenInputView, ...]:
        views = super().render(request)
        _ = (self.root / views[0].relative_path).unlink()
        return views


class _HashDriftRenderer(IndependentRenderer):
    @override
    def render(self, request: ViewRenderRequest) -> tuple[QwenInputView, ...]:
        views = super().render(request)
        _ = (self.root / views[0].relative_path).write_bytes(b"not the hashed asset")
        return views


class _InvalidMediaTypeRenderer(IndependentRenderer):
    @override
    def render(self, request: ViewRenderRequest) -> tuple[QwenInputView, ...]:
        views = super().render(request)
        return replace(views[0], media_type="image/jpeg"), views[1]


class _InvalidPngRenderer(IndependentRenderer):
    @override
    def render(self, request: ViewRenderRequest) -> tuple[QwenInputView, ...]:
        views = super().render(request)
        contents = b"not a PNG"
        _ = (self.root / views[0].relative_path).write_bytes(contents)
        return replace(views[0], asset_hash=sha256(contents).hexdigest()), views[1]


class _WrongOrderRenderer(IndependentRenderer):
    @override
    def render(self, request: ViewRenderRequest) -> tuple[QwenInputView, ...]:
        views = super().render(request)
        return views[1], views[0]


class _InsufficientViewsRenderer(IndependentRenderer):
    @override
    def render(self, request: ViewRenderRequest) -> tuple[QwenInputView, ...]:
        views = super().render(request)
        return views[:1]


@pytest.mark.parametrize(
    ("renderer_type", "expected_code"),
    [
        (_MissingViewRenderer, "view_asset_missing"),
        (_HashDriftRenderer, "view_hash_mismatch"),
        (_InvalidMediaTypeRenderer, "view_media_type_invalid"),
        (_InvalidPngRenderer, "view_png_invalid"),
        (_WrongOrderRenderer, "view_contract_invalid"),
        (_InsufficientViewsRenderer, "view_contract_invalid"),
    ],
)
def test_view_integrity_failure_preserves_specific_code(
    tmp_path: Path,
    renderer_type: type[IndependentRenderer],
    expected_code: str,
) -> None:
    # Given: a renderer that violates one view-asset or view-contract invariant.
    candidate, source = make_candidate(tmp_path)
    request = QwenRefinementRequest(candidate, source, tmp_path, "cpu")

    # When: refinement validates the renderer output.
    result = refine_candidate(
        request,
        renderer_type(tmp_path),
        IndependentBackend(valid_observation_json()),
    )

    # Then: the exact integrity category is retained instead of collapsing to missing.
    assert result.final_success is False
    assert result.failure_code is not None
    assert result.failure_code.value == expected_code


def test_source_hash_drift_preserves_hash_mismatch_code(tmp_path: Path) -> None:
    # Given: a source asset whose bytes changed after its reference was created.
    candidate, source = make_candidate(tmp_path)
    _ = (tmp_path / source.relative_path).write_bytes(b"changed source bytes")
    request = QwenRefinementRequest(candidate, source, tmp_path, "cpu")

    # When: refinement validates the source asset.
    result = refine_candidate(
        request,
        IndependentRenderer(tmp_path),
        IndependentBackend(valid_observation_json()),
    )

    # Then: hash drift is distinct from a missing source asset.
    assert result.failure_code is not None
    assert result.failure_code.value == "source_hash_mismatch"


def test_mask_hash_drift_preserves_hash_mismatch_code(tmp_path: Path) -> None:
    # Given: a mask asset whose bytes changed after its reference was created.
    candidate, source = make_candidate(tmp_path)
    mask_path = tmp_path / candidate.rough_mask.relative_path
    _ = mask_path.write_bytes(b"changed mask bytes")
    request = QwenRefinementRequest(candidate, source, tmp_path, "cpu")

    # When: refinement validates the rough-mask asset.
    result = refine_candidate(
        request,
        IndependentRenderer(tmp_path),
        IndependentBackend(valid_observation_json()),
    )

    # Then: hash drift is distinct from a missing mask asset.
    assert result.failure_code is not None
    assert result.failure_code.value == "mask_hash_mismatch"
