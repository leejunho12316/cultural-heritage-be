from __future__ import annotations

from typing import TYPE_CHECKING

from modules.anomaly_grouping import (
    AnomalyCandidate,
    AnomalyGroupingResult,
    BoundingBox,
    CandidateEvidence,
    RelationClass,
    RelationMergeRequest,
    merge_post_rag_relations,
)
from modules.anomaly_grouping.io import parse_request
from modules.anomaly_grouping.serialization import result_payload
from modules.anomaly_grouping.shared_contracts import (
    JsonObject,
    hybrid_descriptor_payload,
    relation_input_payload,
)
from modules.anomaly_grouping.tests.mask_test_support import rect_mask
from modules.shared import (
    CandidateId,
    ExportCitationBridge,
    HybridDescriptor,
    RagAccountingStatus,
    RelationAuthorityInput,
    RelationAuthorityOutcomeState,
)

if TYPE_CHECKING:
    from pathlib import Path


def _descriptor(candidate_id: CandidateId) -> HybridDescriptor:
    return HybridDescriptor(
        candidate_id=candidate_id,
        concept_family="crack",
        visual_descriptor_tokens=("thin",),
        qwen_selected_terms=("crack",),
        qwen_extracted_descriptors=("thin",),
        concept_card_ids=("card-crack",),
        export_citations=(ExportCitationBridge("citation-1", "exported"),),
        source_cue_ids=("cue-1",),
        provenance_strength="strong",
        evidence_flags=("structured",),
    )


def _relation_input(descriptor: HybridDescriptor) -> RelationAuthorityInput:
    return RelationAuthorityInput(
        candidate_id=descriptor.candidate_id,
        hybrid_descriptor=descriptor,
        geometry_metric_ids=("bbox-iou",),
        same_object_ids=("object-1",),
        source_view_ids=("view-1",),
        duplicate_suppression_key=f"{descriptor.candidate_id}:crack",
        concept_family_compatible=True,
        descriptor_compatible=True,
        citation_provenance_strength="strong",
        rag_status=RagAccountingStatus.COMPLETED,
        evidence_flags=("structured",),
    )


def _candidate(
    tmp_path: Path, candidate_id: str, bbox: BoundingBox
) -> AnomalyCandidate:
    descriptor = _descriptor(CandidateId(candidate_id))
    return AnomalyCandidate(
        candidate_id=CandidateId(candidate_id),
        image_id="image-1",
        source_object_id="object-1",
        source_view_id="view-1",
        seed_lane="owlv2_sam2",
        seed_prompt="crack",
        bbox=bbox,
        mask=rect_mask(tmp_path, candidate_id, bbox),
        evidence=CandidateEvidence(
            concept_family="crack",
            descriptor_tokens=("thin",),
            rag_status=RagAccountingStatus.COMPLETED,
            hybrid_descriptor=descriptor,
            relation_authority_input=_relation_input(descriptor),
        ),
    )


def test_shared_relation_authority_contracts_are_preserved(tmp_path: Path) -> None:
    # Given: candidates carry shared C-004 bridge inputs.
    request = RelationMergeRequest(
        (
            _candidate(tmp_path, "candidate-a", BoundingBox(0, 0, 100, 100)),
            _candidate(tmp_path, "candidate-b", BoundingBox(2, 2, 98, 98)),
        ),
        tmp_path / "masks",
    )

    # When: relation authority emits a duplicate relation.
    result = merge_post_rag_relations(request)
    relation = result.relation_groups[0]

    # Then: shared bridge outcomes are preserved with the relation decision.
    assert relation.relation_class is RelationClass.SAME_ANOMALY_DUPLICATE
    assert len(relation.relation_authority_outcomes) == 2
    assert all(
        outcome.relation_authority_outcome
        is RelationAuthorityOutcomeState.SAME_ANOMALY_DUPLICATE
        for outcome in relation.relation_authority_outcomes
    )
    first_outcome = relation.relation_authority_outcomes[0]
    assert first_outcome.hybrid_descriptor.concept_family == "crack"


def test_serialization_includes_shared_relation_authority_outcomes(
    tmp_path: Path,
) -> None:
    # Given: a relation result carries shared C-004 outcomes.
    request = RelationMergeRequest(
        (
            _candidate(tmp_path, "candidate-a", BoundingBox(0, 0, 100, 100)),
            _candidate(tmp_path, "candidate-b", BoundingBox(2, 2, 98, 98)),
        ),
        tmp_path / "masks",
    )
    result = merge_post_rag_relations(request)

    # When: the result is serialized for the runner artifact.
    payload = result_payload(AnomalyGroupingResult(result, ()))

    # Then: relation authority outcome fields are present in JSON output.
    relation_groups = payload["relation_groups"]
    assert isinstance(relation_groups, list)
    relation_payload = relation_groups[0]
    assert isinstance(relation_payload, dict)
    outcomes = relation_payload["relation_authority_outcomes"]
    assert isinstance(outcomes, list)
    first_outcome_payload = outcomes[0]
    assert isinstance(first_outcome_payload, dict)
    assert (
        first_outcome_payload["relation_authority_outcome"] == "same_anomaly_duplicate"
    )


def test_request_parser_preserves_shared_bridge_fields(tmp_path: Path) -> None:
    # Given: runner JSON carries shared C-004 evidence for one candidate.
    descriptor = _descriptor(CandidateId("candidate-a"))
    relation_input = _relation_input(descriptor)
    mask = rect_mask(tmp_path, "candidate-a", BoundingBox(0, 0, 10, 10))
    payload: JsonObject = {
        "schema": "anomaly_grouping_request_v1",
        "mask_output_dir": str(tmp_path / "masks"),
        "candidates": [
            {
                "bbox_xyxy": [0, 0, 10, 10],
                "candidate_id": "candidate-a",
                "image_id": "image-1",
                "mask": {"path": mask.path, "sha256": mask.sha256},
                "evidence": {
                    "concept_family": "crack",
                    "descriptor_tokens": ["thin"],
                    "hybrid_descriptor": hybrid_descriptor_payload(descriptor),
                    "rag_status": "completed",
                    "relation_authority_input": relation_input_payload(relation_input),
                },
                "seed_lane": "owlv2_sam2",
                "seed_prompt": "crack",
                "source_object_id": "object-1",
                "source_view_id": "view-1",
            }
        ],
    }

    # When: the JSON boundary parser constructs typed candidates.
    request = parse_request(payload)

    # Then: shared descriptor and relation input round-trip into candidate evidence.
    evidence = request.candidates[0].evidence
    assert evidence.hybrid_descriptor == descriptor
    assert evidence.relation_authority_input == relation_input
