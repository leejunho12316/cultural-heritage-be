from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from modules.anomaly_grouping import (
    AnomalyCandidate,
    BoundingBox,
    CandidateEvidence,
    RelationClass,
    RelationMergeRequest,
    merge_post_rag_relations,
)
from modules.anomaly_grouping.tests.mask_test_support import rect_mask
from modules.shared import (
    CandidateId,
    ExportCitationBridge,
    HybridDescriptor,
    RagAccountingStatus,
    RelationAuthorityInput,
)

if TYPE_CHECKING:
    from pathlib import Path


def _candidate(  # noqa: PLR0913
    tmp_path: Path,
    candidate_id: str,
    bbox: BoundingBox,
    concept: str,
    descriptors: tuple[str, ...],
    *,
    seed_prompt: str = "specific defect",
    image_id: str = "image-1",
) -> AnomalyCandidate:
    return AnomalyCandidate(
        candidate_id=CandidateId(candidate_id),
        image_id=image_id,
        source_object_id="object-1",
        source_view_id="view-1",
        seed_lane="owlv2_sam2",
        seed_prompt=seed_prompt,
        bbox=bbox,
        mask=rect_mask(tmp_path, f"{image_id}-{candidate_id}", bbox),
        evidence=CandidateEvidence(
            concept_family=concept,
            descriptor_tokens=descriptors,
            concept_card_ids=(f"card-{concept}",),
            provenance_strength="strong",
            rag_status=RagAccountingStatus.COMPLETED,
        ),
    )


def _candidate_with_relation_input(
    tmp_path: Path,
    candidate_id: str,
    bbox: BoundingBox,
    *,
    concept_compatible: bool,
    descriptor_compatible: bool,
) -> AnomalyCandidate:
    descriptor = HybridDescriptor(
        candidate_id=CandidateId(candidate_id),
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
    relation_input = RelationAuthorityInput(
        candidate_id=CandidateId(candidate_id),
        hybrid_descriptor=descriptor,
        geometry_metric_ids=("bbox-iou",),
        same_object_ids=("object-1",),
        source_view_ids=("view-1",),
        duplicate_suppression_key=f"{candidate_id}:crack",
        concept_family_compatible=concept_compatible,
        descriptor_compatible=descriptor_compatible,
        citation_provenance_strength="strong",
        rag_status=RagAccountingStatus.COMPLETED,
        evidence_flags=("structured",),
    )
    return AnomalyCandidate(
        candidate_id=CandidateId(candidate_id),
        image_id="image-1",
        source_object_id="object-1",
        source_view_id="view-1",
        seed_lane="owlv2_sam2",
        seed_prompt="specific defect",
        bbox=bbox,
        mask=rect_mask(tmp_path, f"relation-input-{candidate_id}", bbox),
        evidence=CandidateEvidence(
            concept_family="crack",
            descriptor_tokens=("thin",),
            concept_card_ids=("card-crack",),
            provenance_strength="strong",
            rag_status=RagAccountingStatus.COMPLETED,
            hybrid_descriptor=descriptor,
            relation_authority_input=relation_input,
        ),
    )


@pytest.mark.parametrize(
    ("left_bbox", "right_bbox", "concept", "descriptors", "expected"),
    [
        (
            BoundingBox(0, 0, 100, 100),
            BoundingBox(2, 2, 98, 98),
            "crack",
            ("thin",),
            RelationClass.SAME_ANOMALY_DUPLICATE,
        ),
        (
            BoundingBox(0, 0, 100, 100),
            BoundingBox(10, 10, 35, 35),
            "flaking",
            ("edge",),
            RelationClass.SAME_ANOMALY_REFINEMENT,
        ),
        (
            # Partial corner overlap: iou~0.087 (< 0.75 duplicate threshold),
            # containment~0.16 (< 0.80 refinement threshold) - overlaps and
            # shares a concept, but neither high-confidence merge subtype
            # applies, so it falls to the catch-all merge class.
            BoundingBox(0, 0, 100, 100),
            BoundingBox(60, 60, 160, 160),
            "damage",
            ("broad",),
            RelationClass.SAME_ANOMALY_ADJACENT,
        ),
    ],
)
def test_relation_authority_classifies_structured_cases(  # noqa: PLR0913, PLR0917
    tmp_path: Path,
    left_bbox: BoundingBox,
    right_bbox: BoundingBox,
    concept: str,
    descriptors: tuple[str, ...],
    expected: RelationClass,
) -> None:
    # Given: two candidates whose masks share the same concept/descriptors and
    # whose bboxes overlap by varying amounts.
    left = _candidate(tmp_path, "candidate-a", left_bbox, concept, descriptors)
    right = _candidate(tmp_path, "candidate-b", right_bbox, concept, descriptors)
    request = RelationMergeRequest((right, left), tmp_path / "masks")

    # When: relation authority evaluates the pair.
    result = merge_post_rag_relations(request)

    # Then: the expected merge subtype is emitted deterministically, and the
    # merged group's kept result carries a materialized union mask.
    assert result.relation_groups[0].relation_class is expected
    assert result.relation_groups[0].parent_candidate_id == CandidateId("candidate-a")
    kept = result.candidate_results[CandidateId("candidate-a")]
    assert kept.kept
    assert kept.mask is not None
    assert not result.candidate_results[CandidateId("candidate-b")].kept


def test_high_iou_concept_mismatch_keeps_both_candidates(tmp_path: Path) -> None:
    # Given: geometry overlaps but structured concept families conflict.
    left = _candidate(
        tmp_path, "candidate-a", BoundingBox(0, 0, 100, 100), "crack", ("thin",)
    )
    right = _candidate(
        tmp_path, "candidate-b", BoundingBox(2, 2, 98, 98), "deposit", ("white",)
    )

    # When: relation authority evaluates the pair.
    result = merge_post_rag_relations(
        RelationMergeRequest((left, right), tmp_path / "masks")
    )

    # Then: overlap alone does not create a same-anomaly merge.
    relation = result.relation_groups[0]
    assert relation.relation_class is RelationClass.CO_LOCATED_DISTINCT_ANOMALY
    assert result.candidate_results[CandidateId("candidate-a")].kept
    assert result.candidate_results[CandidateId("candidate-b")].kept


def test_identical_bbox_across_different_images_is_never_related(
    tmp_path: Path,
) -> None:
    # Given: two candidates with matching geometry and evidence, but drawn
    # from different source images - their bbox coordinates share no common
    # pixel space, so any geometric relation between them would be a
    # coincidence, not a real duplicate/refinement/co-location.
    left = _candidate(
        tmp_path,
        "candidate-a",
        BoundingBox(0, 0, 100, 100),
        "crack",
        ("thin",),
        image_id="image-1",
    )
    right = _candidate(
        tmp_path,
        "candidate-b",
        BoundingBox(0, 0, 100, 100),
        "crack",
        ("thin",),
        image_id="image-2",
    )

    # When: relation authority evaluates the pair.
    result = merge_post_rag_relations(
        RelationMergeRequest((left, right), tmp_path / "masks")
    )

    # Then: no relation is formed, and both candidates remain kept.
    assert result.relation_groups == ()
    assert result.candidate_results[CandidateId("candidate-a")].kept
    assert result.candidate_results[CandidateId("candidate-b")].kept


def test_descriptor_mismatch_keeps_both_candidates(tmp_path: Path) -> None:
    # Given: concept matches but descriptor tokens conflict.
    left = _candidate(
        tmp_path, "candidate-a", BoundingBox(0, 0, 100, 100), "crack", ("thin",)
    )
    right = _candidate(
        tmp_path, "candidate-b", BoundingBox(2, 2, 98, 98), "crack", ("wide",)
    )

    # When: relation authority evaluates the pair.
    result = merge_post_rag_relations(
        RelationMergeRequest((left, right), tmp_path / "masks")
    )

    # Then: descriptor mismatch prevents duplicate/refinement merging.
    assert (
        result.relation_groups[0].relation_class
        is RelationClass.CO_LOCATED_DISTINCT_ANOMALY
    )


def test_non_overlapping_candidates_emit_no_relation_group(tmp_path: Path) -> None:
    # Given: two structured candidates do not overlap or contain each other.
    left = _candidate(
        tmp_path, "candidate-a", BoundingBox(0, 0, 10, 10), "crack", ("thin",)
    )
    right = _candidate(
        tmp_path,
        "candidate-b",
        BoundingBox(20, 20, 30, 30),
        "deposit",
        ("white",),
    )

    # When: relation authority evaluates the unrelated pair.
    result = merge_post_rag_relations(
        RelationMergeRequest((left, right), tmp_path / "masks")
    )

    # Then: no public relation group is emitted and both candidates remain kept.
    assert result.relation_groups == ()
    assert result.candidate_results[CandidateId("candidate-a")].kept
    assert result.candidate_results[CandidateId("candidate-b")].kept


def test_duplicate_cluster_suppressed_children_inherit_kept_parent(
    tmp_path: Path,
) -> None:
    # Given: three nested duplicate candidates form one same-anomaly component.
    candidates = (
        _candidate(
            tmp_path, "candidate-a", BoundingBox(0, 0, 100, 100), "crack", ("thin",)
        ),
        _candidate(
            tmp_path, "candidate-b", BoundingBox(1, 1, 99, 99), "crack", ("thin",)
        ),
        _candidate(
            tmp_path, "candidate-c", BoundingBox(2, 2, 98, 98), "crack", ("thin",)
        ),
    )

    # When: relation authority resolves the component.
    result = merge_post_rag_relations(
        RelationMergeRequest(candidates, tmp_path / "masks")
    )

    # Then: the lowest candidate_id becomes the merged group's canonical id,
    # carrying the mask union, and every other member points to it.
    assert result.candidate_results[CandidateId("candidate-a")].kept
    assert not result.candidate_results[CandidateId("candidate-b")].kept
    assert not result.candidate_results[CandidateId("candidate-c")].kept
    assert result.candidate_results[
        CandidateId("candidate-b")
    ].inherited_parent_candidate_id == CandidateId("candidate-a")
    assert result.candidate_results[
        CandidateId("candidate-c")
    ].inherited_parent_candidate_id == CandidateId("candidate-a")
    assert result.candidate_results[CandidateId("candidate-a")].mask is not None


@pytest.mark.parametrize(
    ("concept_compatible", "descriptor_compatible"),
    [(False, True), (True, False), (False, False)],
)
def test_structured_relation_input_compatibility_blocks_same_anomaly_merge(
    tmp_path: Path,
    concept_compatible: bool,
    descriptor_compatible: bool,
) -> None:
    # Given: raw fallback evidence matches but structured relation flags reject it.
    left = _candidate_with_relation_input(
        tmp_path,
        "candidate-a",
        BoundingBox(0, 0, 100, 100),
        concept_compatible=concept_compatible,
        descriptor_compatible=descriptor_compatible,
    )
    right = _candidate_with_relation_input(
        tmp_path,
        "candidate-b",
        BoundingBox(2, 2, 98, 98),
        concept_compatible=concept_compatible,
        descriptor_compatible=descriptor_compatible,
    )

    # When: relation authority classifies the overlapping pair.
    result = merge_post_rag_relations(
        RelationMergeRequest((left, right), tmp_path / "masks")
    )

    # Then: incompatible structured evidence prevents duplicate suppression.
    assert (
        result.relation_groups[0].relation_class
        is RelationClass.CO_LOCATED_DISTINCT_ANOMALY
    )
    assert result.candidate_results[CandidateId("candidate-a")].kept
    assert result.candidate_results[CandidateId("candidate-b")].kept
