from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from modules.mask_refining import (
    EvidenceFailureCode,
    QwenRefinementRequest,
    parse_observation,
    refine_candidate,
)
from modules.mask_refining.tests.test_support import (
    FakeBackend,
    IndependentBackend,
    IndependentRenderer,
    Selected7Backend,
    make_candidate,
    valid_observation_json,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("raw_output", "failure_code"),
    [
        ("{", EvidenceFailureCode.MALFORMED_OUTPUT),
        ("{}", EvidenceFailureCode.MISSING_OUTPUT_FIELD),
        (
            valid_observation_json().replace(
                '"selected_terms":["brown region"],',
                "",
            ),
            EvidenceFailureCode.MISSING_OUTPUT_FIELD,
        ),
        (
            valid_observation_json().replace(
                '"extracted_descriptors":["irregular","brown"],',
                "",
            ),
            EvidenceFailureCode.MISSING_OUTPUT_FIELD,
        ),
        (
            valid_observation_json().replace('"morphology":"spot",', ""),
            EvidenceFailureCode.MISSING_OUTPUT_FIELD,
        ),
        (
            valid_observation_json()
            .replace('"selected_terms":["brown region"]', '"selected_terms":[]')
            .replace(
                '"extracted_descriptors":["irregular","brown"]',
                '"extracted_descriptors":[]',
            ),
            EvidenceFailureCode.INVALID_QUERY_FIELD,
        ),
        (
            valid_observation_json().replace('"confidence":0.84', '"confidence":1.1'),
            EvidenceFailureCode.INVALID_CONFIDENCE,
        ),
    ],
)
def test_observation_parser_returns_explicit_failure_for_untrusted_output(
    tmp_path: Path, raw_output: str, failure_code: EvidenceFailureCode
) -> None:
    # Given: malformed, incomplete, or invalid-confidence model JSON.
    renderer = IndependentRenderer(tmp_path)
    candidate, source = make_candidate(tmp_path)
    request = QwenRefinementRequest(candidate, source, tmp_path, "cpu")
    views = renderer.render(request.to_view_request())

    # When: the parser consumes the boundary response.
    result = parse_observation(raw_output, views)

    # Then: it returns a reportable failed result, never None.
    assert result.final_success is False
    assert result.failure_code is failure_code
    assert result.failed_stage.value == "observation_parsing"
    assert result.report_display_text == "없음"


@pytest.mark.parametrize(
    "backend",
    [
        FakeBackend(valid_observation_json()),
        Selected7Backend(valid_observation_json()),
        IndependentBackend(valid_observation_json(), device="unsupported"),
    ],
)
def test_non_independent_or_unsupported_qwen_backend_fails_closed(
    tmp_path: Path,
    backend: IndependentBackend | FakeBackend | Selected7Backend,
) -> None:
    # Given: a test, selected7, or unsupported-device backend configuration.
    candidate, source = make_candidate(tmp_path)
    request = QwenRefinementRequest(candidate, source, tmp_path, "cpu")

    # When: the accepted candidate reaches Qwen refinement.
    result = refine_candidate(request, IndependentRenderer(tmp_path), backend)

    # Then: no fake or unsupported execution can become final success.
    assert result.final_success is False
    assert result.failure_code in {
        EvidenceFailureCode.BACKEND_NOT_INDEPENDENT,
        EvidenceFailureCode.UNSUPPORTED_DEVICE,
    }


def test_cache_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    # Given: a nominally independent backend with stale cache identity evidence.
    candidate, source = make_candidate(tmp_path)
    request = QwenRefinementRequest(candidate, source, tmp_path, "cpu")

    # When: it returns an output response with a mismatched cache hash.
    result = refine_candidate(
        request,
        IndependentRenderer(tmp_path),
        IndependentBackend(valid_observation_json(), cache_hash="stale"),
    )

    # Then: cache-accounting drift is explicit and final success is false.
    assert result.final_success is False
    assert result.failure_code is EvidenceFailureCode.CACHE_HASH_MISMATCH


def test_query_driving_qwen_terms_are_normalized_at_boundary(
    tmp_path: Path,
) -> None:
    # Given: valid Qwen JSON with query fields that need canonical spacing/case.
    renderer = IndependentRenderer(tmp_path)
    candidate, source = make_candidate(tmp_path)
    request = QwenRefinementRequest(candidate, source, tmp_path, "cpu")
    views = renderer.render(request.to_view_request())
    raw_output = valid_observation_json().replace(
        '"selected_terms":["brown region"]',
        '"selected_terms":["  Brown   Region  "]',
    )

    # When: the untrusted model response crosses the parser boundary.
    result = parse_observation(raw_output, views)

    # Then: only normalized query-driving terms reach the bridge seam.
    assert result.final_success is True
    assert result.selected_terms == ("brown region",)


@pytest.mark.parametrize(
    "morphology",
    [
        "line",
        "spot",
        "hole_pit",
        "crust",
        "powder",
        "flaking_patch",
        "broad_patch",
        "unknown",
    ],
)
def test_observation_parser_accepts_qwen_morphology_allowlist(
    tmp_path: Path, morphology: str
) -> None:
    # Given: Qwen output with one allowed mask-internal morphology value.
    renderer = IndependentRenderer(tmp_path)
    candidate, source = make_candidate(tmp_path)
    request = QwenRefinementRequest(candidate, source, tmp_path, "cpu")
    views = renderer.render(request.to_view_request())
    raw_output = valid_observation_json().replace(
        '"morphology":"spot"',
        f'"morphology":"{morphology}"',
    )

    # When: the untrusted model response crosses the parser boundary.
    result = parse_observation(raw_output, views)

    # Then: the typed local evidence retains only the allowlisted morphology.
    assert result.final_success is True
    assert result.morphology == morphology


@pytest.mark.parametrize(
    "morphology",
    [
        "",
        "unknown",
        "unknown morphology",
        "smooth",
        "shiny",
        "polished",
        "round",
        "metallic",
        "wood",
        "stone",
        "surface",
    ],
)
def test_observation_parser_normalizes_generic_morphology_to_unknown(
    tmp_path: Path, morphology: str
) -> None:
    # Given: Qwen calls an object, material, or generic surface term morphology.
    renderer = IndependentRenderer(tmp_path)
    candidate, source = make_candidate(tmp_path)
    request = QwenRefinementRequest(candidate, source, tmp_path, "cpu")
    views = renderer.render(request.to_view_request())
    raw_output = valid_observation_json().replace(
        '"morphology":"spot"',
        f'"morphology":"{morphology}"',
    )

    # When: the untrusted model response crosses the parser boundary.
    result = parse_observation(raw_output, views)

    # Then: generic descriptors cannot become query-driving morphology.
    assert result.final_success is True
    assert result.morphology == "unknown"


def test_observation_parser_rejects_unsupported_morphology(tmp_path: Path) -> None:
    # Given: Qwen supplies unsupported text in its required morphology field.
    renderer = IndependentRenderer(tmp_path)
    candidate, source = make_candidate(tmp_path)
    request = QwenRefinementRequest(candidate, source, tmp_path, "cpu")
    views = renderer.render(request.to_view_request())
    raw_output = valid_observation_json().replace(
        '"morphology":"spot"',
        '"morphology":"triangle"',
    )

    # When: the parser consumes the untrusted response.
    result = parse_observation(raw_output, views)

    # Then: unsupported morphology fails closed before RAG sees it.
    assert result.final_success is False
    assert result.failure_code is EvidenceFailureCode.INVALID_QUERY_FIELD


def test_observation_parser_accepts_fenced_single_json_object(tmp_path: Path) -> None:
    # Given: Qwen returns the one valid object wrapped in a markdown JSON fence.
    renderer = IndependentRenderer(tmp_path)
    candidate, source = make_candidate(tmp_path)
    request = QwenRefinementRequest(candidate, source, tmp_path, "cpu")
    views = renderer.render(request.to_view_request())
    raw_output = f"```json\n{valid_observation_json()}\n```"

    # When: the untrusted model response crosses the parser boundary.
    result = parse_observation(raw_output, views)

    # Then: fence formatting is ignored, but the object fields remain enforced.
    assert result.final_success is True
    assert result.selected_terms == ("brown region",)


def test_query_driving_qwen_terms_reject_unsafe_text(tmp_path: Path) -> None:
    # Given: Qwen output containing path/query metacharacters in a query driver.
    renderer = IndependentRenderer(tmp_path)
    candidate, source = make_candidate(tmp_path)
    request = QwenRefinementRequest(candidate, source, tmp_path, "cpu")
    views = renderer.render(request.to_view_request())
    raw_output = valid_observation_json().replace(
        '"selected_terms":["brown region"]',
        '"selected_terms":["../etc/passwd"]',
    )

    # When: the parser handles the untrusted query-driving field.
    result = parse_observation(raw_output, views)

    # Then: unsafe text fails closed before reaching RAG query construction.
    assert result.final_success is False
    assert result.failure_code is EvidenceFailureCode.INVALID_QUERY_FIELD
