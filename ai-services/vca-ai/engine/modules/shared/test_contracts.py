from __future__ import annotations

import pytest

from modules.shared import (
    ACTIVE_DETECTOR_LANES,
    CANDIDATE_METADATA_BACKEND_ID,
    CLI_SCHEMA_VERSION,
    DETECTOR_ADAPTER_SCHEMA_VERSION,
    FINAL_REPORT_SCHEMA_VERSION,
    FINAL_REPORT_STATIC_VERIFICATION_SCHEMA_VERSION,
    FINDING_SCHEMA_VERSION,
    INPUT_MANIFEST_SCHEMA_VERSION,
    OBSERVATION_POLICY_ID,
    OBSERVATION_POLICY_VERSION,
    PROMPT_ID,
    PROMPT_VERSION,
    QWEN_BACKEND_DEPENDENCY,
    QWEN_BACKEND_KIND,
    QWEN_MODEL_ID,
    RAG_CONCEPT_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    REPORT_STATIC_VERIFICATION_SCHEMA_VERSION,
    USER_FOLLOWUP_REQUEST_SCHEMA_VERSION,
    VOCABULARY_VERSION,
    CandidateId,
    ContractValidationError,
    DetectorLane,
    FollowupSelectorType,
    PromptMetadata,
    PromptRole,
    RagLane,
    UserFollowupRequest,
    detector_to_rag_lane,
    followup_request_hash,
    parse_detector_lane,
    parse_followup_selector_type,
    parse_user_followup_schema_version,
    stable_empty_followup_request_hash,
    validate_active_detector_lanes,
)


def _request(*, requested_at: str = "2026-07-31T00:00:00Z") -> UserFollowupRequest:
    return UserFollowupRequest(
        schema_version=USER_FOLLOWUP_REQUEST_SCHEMA_VERSION,
        request_id="request-001",
        selector_type=FollowupSelectorType.CANDIDATE_ID,
        selector_id="candidate-001",
        followup_reason="inspect visual evidence",
        trigger_priority=10,
        requester="reviewer",
        requested_at=requested_at,
        dry_run_only=False,
        budget_scope=("candidate_only",),
        provenance=("trace-verified",),
    )


def test_schema_and_qwen_constants_match_locked_contract() -> None:
    # Given: the shared single-source contract.
    schema_versions = (
        INPUT_MANIFEST_SCHEMA_VERSION,
        DETECTOR_ADAPTER_SCHEMA_VERSION,
        FINDING_SCHEMA_VERSION,
        REPORT_SCHEMA_VERSION,
        FINAL_REPORT_SCHEMA_VERSION,
        CLI_SCHEMA_VERSION,
        REPORT_STATIC_VERIFICATION_SCHEMA_VERSION,
        FINAL_REPORT_STATIC_VERIFICATION_SCHEMA_VERSION,
        RAG_CONCEPT_SCHEMA_VERSION,
    )

    # When: required schema and Qwen values are read.
    qwen_values = (
        QWEN_BACKEND_KIND,
        QWEN_BACKEND_DEPENDENCY,
        CANDIDATE_METADATA_BACKEND_ID,
        QWEN_MODEL_ID,
        PROMPT_ID,
        PROMPT_VERSION,
        VOCABULARY_VERSION,
        OBSERVATION_POLICY_ID,
        OBSERVATION_POLICY_VERSION,
    )

    # Then: their byte-for-byte public values remain locked.
    assert schema_versions == (
        "anomaly-input-manifest-v1",
        "raw-image-detector-adapter-v1",
        "raw-image-finding-v1",
        "raw-image-anomaly-report-v1",
        "raw-image-final-report-v1",
        "raw-image-anomaly-cli-v1",
        "raw-image-anomaly-report-static-v1",
        "raw-image-final-report-static-v1",
        "rag-visual-concept-v1",
    )
    assert qwen_values == (
        "independent_raw_image",
        "independent_raw_image_qwen_backend",
        "independent-raw-image-qwen",
        "Qwen/Qwen2.5-VL-3B-Instruct",
        "raw-image-visual-observation",
        "v1",
        "qwen-controlled-vocabulary-v1",
        "visual-only",
        "v1",
    )


def test_detector_lanes_map_to_rag_lanes_and_reject_clipseg() -> None:
    # Given: every allowed active detector lane.
    requested_lanes = ACTIVE_DETECTOR_LANES

    # When: lanes are validated and mapped for RAG.
    validated_lanes = validate_active_detector_lanes(requested_lanes)
    mapped_lanes = tuple(detector_to_rag_lane(lane) for lane in validated_lanes)

    # Then: all mappings are exact and CLIPSeg remains non-active.
    assert mapped_lanes == (
        RagLane.OWLV2,
        RagLane.FLORENCE2,
        RagLane.GROUNDINGDINO,
    )
    with pytest.raises(ContractValidationError):
        _ = validate_active_detector_lanes((DetectorLane.CLIPSEG,))


def test_prompt_metadata_and_followup_selectors_are_strict() -> None:
    # Given: a valid reusable prompt metadata value and user follow-up request.
    metadata = PromptMetadata(
        prompt_pack_id="seed-pack",
        prompt_role=PromptRole.STATIC_SEED,
        model_lane=RagLane.OWLV2,
        generated_prompt_id="prompt-001",
        source_terms=("surface anomaly",),
    )
    request = _request()

    # When: their typed contract fields are inspected.
    request_hash = followup_request_hash((request,))

    # Then: valid values remain serializable and malformed selector/schema input fails.
    assert metadata.prompt_role is PromptRole.STATIC_SEED
    assert request_hash != stable_empty_followup_request_hash()
    with pytest.raises(ContractValidationError):
        _ = parse_followup_selector_type("unsupported")
    with pytest.raises(ContractValidationError):
        _ = parse_detector_lane("invalid_lane")
    with pytest.raises(ContractValidationError):
        _ = parse_user_followup_schema_version("user-followup-request-v2")
    with pytest.raises(ContractValidationError):
        _ = UserFollowupRequest(
            schema_version=USER_FOLLOWUP_REQUEST_SCHEMA_VERSION,
            request_id="request-002",
            selector_type=FollowupSelectorType.OBJECT_ID,
            selector_id="",
            followup_reason="inspect",
            trigger_priority=1,
            requester="reviewer",
            requested_at="2026-07-31T00:00:00Z",
            dry_run_only=True,
        )


def test_followup_hash_uses_normalized_records_not_request_timestamps() -> None:
    # Given: logically identical requests recorded at different wall-clock times.
    first_request = _request(requested_at="2026-07-31T00:00:00Z")
    second_request = _request(requested_at="2030-01-01T12:00:00Z")

    # When: hashes are computed before parent-target resolution.
    first_hash = followup_request_hash((first_request,))
    second_hash = followup_request_hash((second_request,))

    # Then: excluded recording metadata cannot perturb deterministic planning input.
    assert first_hash == second_hash
    assert CandidateId("candidate-001") == "candidate-001"
