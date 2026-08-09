from __future__ import annotations

from dataclasses import fields, replace
from typing import TYPE_CHECKING

import pytest

from modules.mask_refining import (
    OBSERVATION_POLICY_ID,
    OBSERVATION_POLICY_VERSION,
    PROMPT_ID,
    PROMPT_VERSION,
    QWEN_MODEL_ID,
    VOCABULARY_VERSION,
    QwenInputView,
    QwenRefinementRequest,
    QwenViewKind,
    parse_observation,
    refine_candidate,
)
from modules.mask_refining.tests.test_support import (
    FakeRenderer,
    IndependentBackend,
    make_candidate,
    valid_observation_json,
)
from modules.shared import (
    BridgeFieldRole,
    BridgeFieldUsage,
    CandidateId,
    QwenBridgeStatus,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_observation_parser_preserves_visual_only_query_and_provenance_fields() -> None:
    # Given: valid visual-only Qwen JSON and two locked input-view references.
    views = (
        QwenInputView(
            view_id="candidate-001:masked",
            kind=QwenViewKind.MASKED_TARGET_CROP,
            asset_hash="a" * 64,
            media_type="image/png",
            relative_path="views/masked.png",
            call_order=1,
        ),
        QwenInputView(
            view_id="candidate-001:bounded",
            kind=QwenViewKind.BOUNDED_PADDED_CANDIDATE_CROP,
            asset_hash="b" * 64,
            media_type="image/png",
            relative_path="views/bounded.png",
            call_order=2,
        ),
    )

    # When: the untrusted model response crosses the observation boundary.
    result = parse_observation(valid_observation_json(), views)

    # Then: query-driving values and immutable provenance are retained exactly.
    assert result.final_success is True
    assert result.model_id == QWEN_MODEL_ID
    assert (result.prompt_id, result.prompt_version, result.vocabulary_version) == (
        PROMPT_ID,
        PROMPT_VERSION,
        VOCABULARY_VERSION,
    )
    assert (result.observation_policy_id, result.observation_policy_version) == (
        OBSERVATION_POLICY_ID,
        OBSERVATION_POLICY_VERSION,
    )
    assert result.selected_terms == ("brown region",)
    assert result.extracted_descriptors == ("irregular", "brown")
    assert result.morphology == "spot"
    assert (
        result.field_roles[BridgeFieldRole.SELECTED_TERMS]
        is BridgeFieldUsage.QUERY_DRIVING
    )
    assert (
        result.field_roles[BridgeFieldRole.EXTRACTED_DESCRIPTORS]
        is BridgeFieldUsage.QUERY_DRIVING
    )
    assert (
        result.field_roles[BridgeFieldRole.CONFIDENCE]
        is BridgeFieldUsage.PROVENANCE_ONLY
    )
    assert result.input_view_ids == ("candidate-001:masked", "candidate-001:bounded")
    assert result.input_view_hashes == ("a" * 64, "b" * 64)


def test_qwen_evidence_converts_to_shared_bridge_result() -> None:
    # Given: parsed local Qwen evidence with a candidate identity attached later.
    views = (
        QwenInputView(
            view_id="candidate-001:masked",
            kind=QwenViewKind.MASKED_TARGET_CROP,
            asset_hash="a" * 64,
            media_type="image/png",
            relative_path="views/masked.png",
            call_order=1,
        ),
        QwenInputView(
            view_id="candidate-001:bounded",
            kind=QwenViewKind.BOUNDED_PADDED_CANDIDATE_CROP,
            asset_hash="b" * 64,
            media_type="image/png",
            relative_path="views/bounded.png",
            call_order=2,
        ),
    )
    local_result = parse_observation(valid_observation_json(), views)
    localized = replace(local_result, candidate_id=CandidateId("candidate-001"))

    # When: downstream asks for the shared C-004 bridge handoff record.
    bridge_result = localized.to_bridge_result()

    # Then: shared query-driving seam is the only RAG query source.
    assert bridge_result.status is QwenBridgeStatus.SUCCESS
    assert bridge_result.query_driving_fields() == (
        ("brown region",),
        ("irregular", "brown", "spot"),
    )
    rag_query_input = bridge_result.to_rag_query_input()
    assert tuple(field.name for field in fields(rag_query_input)) == (
        "candidate_id",
        "selected_terms",
        "extracted_descriptors",
    )
    assert (
        rag_query_input.candidate_id,
        rag_query_input.selected_terms,
        rag_query_input.extracted_descriptors,
    ) == (
        CandidateId("candidate-001"),
        ("brown region",),
        ("irregular", "brown", "spot"),
    )


@pytest.mark.parametrize(
    ("morphology", "expected_descriptors"),
    [
        ("line", ("line",)),
        ("spot", ("spot",)),
        ("hole_pit", ("hole", "pit")),
        ("crust", ("crust",)),
        ("powder", ("powder",)),
        ("flaking_patch", ("flaking",)),
        ("broad_patch", ("broad",)),
        ("unknown", ()),
    ],
)
def test_qwen_morphology_uses_existing_bridge_descriptors(
    tmp_path: Path, morphology: str, expected_descriptors: tuple[str, ...]
) -> None:
    # Given: local Qwen evidence with an allowlisted morphology value.
    candidate, source = make_candidate(tmp_path)
    request = QwenRefinementRequest(candidate, source, tmp_path, "cpu")
    views = FakeRenderer(tmp_path).render(request.to_view_request())
    raw_output = valid_observation_json().replace(
        '"morphology":"spot"',
        f'"morphology":"{morphology}"',
    )
    local_result = replace(
        parse_observation(raw_output, views),
        candidate_id=CandidateId("candidate-001"),
    )

    # When: the local observation crosses the shared bridge boundary.
    bridge_result = local_result.to_bridge_result()

    # Then: morphology adds only downstream-safe existing descriptor tokens.
    assert bridge_result.selected_terms == ("brown region",)
    assert bridge_result.extracted_descriptors == (
        "irregular",
        "brown",
        *expected_descriptors,
    )


def test_failed_qwen_evidence_preserves_failure_at_bridge_handoff(
    tmp_path: Path,
) -> None:
    # Given: a candidate whose renderer fails the production provenance gate.
    candidate, source = make_candidate(tmp_path)
    request = QwenRefinementRequest(candidate, source, tmp_path, "cpu")
    failed_result = refine_candidate(
        request,
        FakeRenderer(tmp_path),
        IndependentBackend(valid_observation_json()),
    )

    # When: downstream converts the failed evidence to its bridge record.
    bridge_result = failed_result.to_bridge_result()

    # Then: accounting receives a non-null failed result with no query inputs.
    assert bridge_result is not None
    assert bridge_result.status is QwenBridgeStatus.FAILED
    assert bridge_result.selected_terms == ()
    assert bridge_result.extracted_descriptors == ()
    assert bridge_result.failure_code == "renderer_not_independent"


def test_qwen_input_view_locks_two_view_prompt_and_rendering_metadata() -> None:
    # Given: one Qwen input view with the required renderer metadata.
    view = QwenInputView(
        view_id="candidate-001:masked",
        kind=QwenViewKind.MASKED_TARGET_CROP,
        asset_hash="a" * 64,
        media_type="image/png",
        relative_path="views/masked.png",
        call_order=1,
    )

    # When: the view contract is inspected.
    metadata = (
        view.padding_px,
        view.max_area_px,
        view.focused_mask_overlay_mode,
        view.merge_policy,
        view.prompt_id,
        view.prompt_version,
    )

    # Then: fixed evidence settings cannot silently drift.
    assert metadata == (
        4,
        1024,
        "focused_mask_overlay",
        "masked_target_primary_with_bounded_context",
        PROMPT_ID,
        PROMPT_VERSION,
    )
