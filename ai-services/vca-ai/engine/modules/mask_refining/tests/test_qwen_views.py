from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, override

from modules.mask_refining import (
    EvidenceFailureCode,
    FileQwenViewRenderer,
    QwenRefinementRequest,
    refine_candidate,
)
from modules.mask_refining.rendering.views import renderer_is_independent
from modules.mask_refining.tests.test_support import (
    FakeRenderer,
    IndependentBackend,
    IndependentRenderer,
    make_asset,
    make_candidate,
    valid_observation_json,
)
from modules.shared import CandidateId

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


def test_refinement_creates_exactly_two_ordered_visual_input_views(
    tmp_path: Path,
) -> None:
    # Given: valid source/mask assets and independent production seams.
    candidate, source = make_candidate(tmp_path)
    request = QwenRefinementRequest(candidate, source, tmp_path, "cpu")

    # When: the accepted detector candidate is refined.
    result = refine_candidate(
        request,
        IndependentRenderer(tmp_path),
        IndependentBackend(valid_observation_json()),
    )

    # Then: Qwen receives exactly the locked masked and bounded visual views.
    assert result.final_success is True
    assert tuple(view.kind.value for view in result.input_views) == (
        "masked_target_crop",
        "bounded_padded_candidate_crop",
    )
    assert tuple(view.call_order for view in result.input_views) == (1, 2)


def test_file_renderer_materializes_placeholder_views_with_clipped_crop_metadata(
    tmp_path: Path,
) -> None:
    # Given: a candidate near the source edge and the placeholder file renderer.
    candidate, source = make_candidate(tmp_path)
    edge_candidate = replace(candidate, bbox_xyxy=(0.0, 1.0, 30.0, 20.0))
    request = QwenRefinementRequest(edge_candidate, source, tmp_path, "cpu")
    renderer = FileQwenViewRenderer(tmp_path)

    # When: the file renderer directly materializes deterministic view assets.
    views = renderer.render(request.to_view_request())

    # Then: clipped metadata is valid, but the synthetic renderer is not independent.
    assert renderer_is_independent(renderer) is False
    assert views[0].crop_xyxy == (0.0, 0.0, 34.0, 24.0)
    left, top, right, bottom = views[0].crop_xyxy
    assert (right - left) * (bottom - top) <= views[0].max_area_px
    assert all((tmp_path / view.relative_path).is_file() for view in views)


def test_file_renderer_cannot_claim_production_refinement_success(
    tmp_path: Path,
) -> None:
    # Given: valid assets and the deterministic placeholder file renderer.
    candidate, source = make_candidate(tmp_path)
    request = QwenRefinementRequest(candidate, source, tmp_path, "cpu")

    # When: placeholder views reach the refinement boundary.
    result = refine_candidate(
        request,
        FileQwenViewRenderer(tmp_path),
        IndependentBackend(valid_observation_json()),
    )

    # Then: synthetic assets are retained for inspection but fail production provenance.
    assert result.final_success is False
    assert result.failure_code is EvidenceFailureCode.RENDERER_NOT_INDEPENDENT


def test_file_renderer_rejects_path_escape_before_materializing_views(
    tmp_path: Path,
) -> None:
    # Given: a malformed candidate id that would escape through the view filename.
    asset_root = tmp_path / "assets"
    candidate, source = make_candidate(asset_root)
    unsafe_candidate = replace(candidate, candidate_id=CandidateId("../../escaped"))
    request = QwenRefinementRequest(unsafe_candidate, source, asset_root, "cpu")

    # When: the file renderer is used through the refinement boundary.
    result = refine_candidate(
        request,
        FileQwenViewRenderer(asset_root),
        IndependentBackend(valid_observation_json()),
    )

    # Then: no file is written outside the asset root and the result fails closed.
    assert result.final_success is False
    assert result.failure_code is EvidenceFailureCode.PATH_ESCAPE
    assert not (tmp_path / "escaped:masked_target_crop.png").exists()


def test_test_renderer_cannot_claim_production_refinement_success(
    tmp_path: Path,
) -> None:
    # Given: a test-only renderer that otherwise creates valid-looking views.
    candidate, source = make_candidate(tmp_path)
    request = QwenRefinementRequest(candidate, source, tmp_path, "cpu")

    # When: the test seam enters the refinement path.
    result = refine_candidate(
        request,
        FakeRenderer(tmp_path),
        IndependentBackend(valid_observation_json()),
    )

    # Then: it emits an explicit failure record rather than a success claim.
    assert result.final_success is False
    assert result.failure_code is EvidenceFailureCode.RENDERER_NOT_INDEPENDENT
    assert result.report_display_text == "없음"


def test_missing_or_hash_drifted_view_assets_fail_closed(tmp_path: Path) -> None:
    # Given: renderers that return stale view asset references.
    candidate, source = make_candidate(tmp_path)
    request = QwenRefinementRequest(candidate, source, tmp_path, "cpu")

    # When: refinement validates missing and hash-drifted view assets.
    missing = refine_candidate(
        request,
        _MissingViewRenderer(tmp_path),
        IndependentBackend(valid_observation_json()),
    )
    drifted = refine_candidate(
        request,
        _HashDriftRenderer(tmp_path),
        IndependentBackend(valid_observation_json()),
    )

    # Then: stale view evidence cannot become final success.
    assert missing.final_success is False
    assert missing.failure_code is EvidenceFailureCode.VIEW_ASSET_MISSING
    assert drifted.final_success is False
    assert drifted.failure_code is not None
    assert drifted.failure_code.value == "view_hash_mismatch"


def test_invalid_mask_and_crop_outside_source_fail_closed(tmp_path: Path) -> None:
    # Given: candidates with an invalid mask PNG or bbox outside source dimensions.
    candidate, source = make_candidate(tmp_path)
    invalid_mask = make_asset(tmp_path, "masks/bad.png", b"bad", "image/png")
    bad_mask_candidate = replace(candidate, rough_mask=invalid_mask)
    outside_candidate = replace(candidate, bbox_xyxy=(10.0, 12.0, 140.0, 48.0))

    # When: refinement validates both candidates before backend invocation.
    invalid_mask_result = refine_candidate(
        QwenRefinementRequest(bad_mask_candidate, source, tmp_path, "cpu"),
        FileQwenViewRenderer(tmp_path),
        IndependentBackend(valid_observation_json()),
    )
    outside_crop_result = refine_candidate(
        QwenRefinementRequest(outside_candidate, source, tmp_path, "cpu"),
        FileQwenViewRenderer(tmp_path),
        IndependentBackend(valid_observation_json()),
    )

    # Then: required failure QA cases produce explicit failed Qwen records.
    assert invalid_mask_result.final_success is False
    assert invalid_mask_result.failure_code is EvidenceFailureCode.INVALID_MASK
    assert outside_crop_result.final_success is False
    assert outside_crop_result.failure_code is EvidenceFailureCode.INVALID_BBOX


def test_large_valid_source_crop_reaches_renderer_and_backend(tmp_path: Path) -> None:
    # Given: a valid candidate whose source-space crop exceeds the view budget.
    candidate, source = make_candidate(tmp_path)
    oversized_candidate = replace(candidate, bbox_xyxy=(10.0, 10.0, 60.0, 50.0))
    request = QwenRefinementRequest(oversized_candidate, source, tmp_path, "cpu")

    # When: refinement renders the candidate into budgeted Qwen views.
    result = refine_candidate(
        request,
        IndependentRenderer(tmp_path),
        IndependentBackend(valid_observation_json()),
    )

    # Then: source crop size does not reject an otherwise valid candidate.
    assert result.final_success is True
