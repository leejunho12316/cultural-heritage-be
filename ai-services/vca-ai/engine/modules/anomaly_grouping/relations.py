"""Post-RAG relation authority."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from modules.anomaly_grouping.geometry import area_ratio, containment, iou, overlaps
from modules.anomaly_grouping.ids import relation_group_id, reopen_event_id
from modules.anomaly_grouping.models import (
    AnomalyCandidate,
    FinalReopenRejection,
    MergePhase,
    PreviousSuppression,
    RelationClass,
    RelationGroup,
    RelationMergeRequest,
    RelationMergeResult,
    ReopenEvent,
)
from modules.anomaly_grouping.relation_results import candidate_results
from modules.anomaly_grouping.shared_contracts import relation_outcomes
from modules.shared import RagAccountingStatus

if TYPE_CHECKING:
    from collections.abc import Mapping

    from modules.shared import CandidateId, RelationAuthorityInput

_REOPEN_REASON: Final = "co_located_distinct_after_suppression"
_DUPLICATE_IOU_THRESHOLD: Final = 0.75
_REFINEMENT_CONTAINMENT_THRESHOLD: Final = 0.80
_REFINEMENT_AREA_RATIO_THRESHOLD: Final = 0.35


def merge_post_rag_relations(request: RelationMergeRequest) -> RelationMergeResult:
    """Merge post-RAG candidates without geometry-only deletion."""
    candidates = tuple(
        sorted(request.candidates, key=lambda candidate: candidate.candidate_id)
    )
    relation_groups = tuple(
        group
        for left, right in _pairs(candidates)
        if (group := _relation_group(left, right)) is not None
    )
    reopen_events, final_rejections = _reopen_outputs(request, relation_groups)
    return RelationMergeResult(
        request.phase,
        relation_groups,
        candidate_results(candidates, relation_groups),
        reopen_events,
        final_rejections,
    )


def _relation_group(
    left: AnomalyCandidate,
    right: AnomalyCandidate,
) -> RelationGroup | None:
    parent, child = _parent_child(left, right)
    relation_class = _classify(parent, child)
    if relation_class is None:
        return None
    sources = tuple(sorted((parent.candidate_id, child.candidate_id)))
    return RelationGroup(
        relation_group_id(
            relation_class, parent.candidate_id, child.candidate_id, sources
        ),
        relation_class,
        parent.candidate_id,
        child.candidate_id,
        sources,
        _reasons(parent, child, relation_class),
        relation_outcomes(relation_class, (parent, child)),
    )


def _classify(
    parent: AnomalyCandidate,
    child: AnomalyCandidate,
) -> RelationClass | None:
    concept_match = _concept_match(parent, child)
    descriptor_match = _descriptor_match(parent, child)
    pair_iou = iou(parent.bbox, child.bbox)
    child_containment = containment(child.bbox, parent.bbox)
    if pair_iou >= _DUPLICATE_IOU_THRESHOLD and concept_match and descriptor_match:
        return RelationClass.SAME_ANOMALY_DUPLICATE
    if (
        child_containment >= _REFINEMENT_CONTAINMENT_THRESHOLD
        and area_ratio(child.bbox, parent.bbox) <= _REFINEMENT_AREA_RATIO_THRESHOLD
    ):
        if concept_match and descriptor_match:
            return RelationClass.SAME_ANOMALY_REFINEMENT
        return RelationClass.CONTEXT_CONTAINS
    if overlaps(parent.bbox, child.bbox):
        return RelationClass.CO_LOCATED_DISTINCT_ANOMALY
    return None


def _reopen_outputs(
    request: RelationMergeRequest,
    relation_groups: tuple[RelationGroup, ...],
) -> tuple[tuple[ReopenEvent, ...], tuple[FinalReopenRejection, ...]]:
    suppressions = {
        suppression.candidate_id: suppression
        for suppression in request.previous_suppressions
    }
    reopened = frozenset(request.already_reopened_candidate_ids)
    events: list[ReopenEvent] = []
    rejections: list[FinalReopenRejection] = []
    handled_candidate_ids: set[CandidateId] = set()
    for relation in relation_groups:
        match relation.relation_class:
            case RelationClass.CO_LOCATED_DISTINCT_ANOMALY:
                pass
            case (
                RelationClass.SAME_ANOMALY_DUPLICATE
                | RelationClass.SAME_ANOMALY_REFINEMENT
                | RelationClass.CONTEXT_CONTAINS
            ):
                continue
        suppression = _relation_suppression(relation, suppressions)
        if suppression is None:
            continue
        if suppression.candidate_id in handled_candidate_ids:
            continue
        handled_candidate_ids.add(suppression.candidate_id)
        match request.phase:
            case MergePhase.FINAL_RELATION_MERGE:
                rejections.append(
                    _rejection(suppression, RagAccountingStatus.REOPEN_FORBIDDEN_FINAL)
                )
            case MergePhase.INITIAL_RELATION_MERGE:
                if suppression.candidate_id in reopened:
                    rejections.append(
                        _rejection(suppression, RagAccountingStatus.REOPEN_SKIPPED)
                    )
                    continue
                events.append(
                    ReopenEvent(
                        reopen_event_id(
                            suppression.candidate_id,
                            suppression.parent_candidate_id,
                            suppression.same_anomaly_group_id,
                            _REOPEN_REASON,
                        ),
                        suppression.candidate_id,
                        suppression.parent_candidate_id,
                        suppression.same_anomaly_group_id,
                        _REOPEN_REASON,
                    )
                )
    return tuple(events), tuple(rejections)


def _relation_suppression(
    relation: RelationGroup,
    suppressions: Mapping[CandidateId, PreviousSuppression],
) -> PreviousSuppression | None:
    relation_candidate_ids = frozenset(
        (relation.child_candidate_id, relation.parent_candidate_id)
    )
    for suppression in suppressions.values():
        suppression_pair = frozenset(
            (suppression.candidate_id, suppression.parent_candidate_id)
        )
        if relation_candidate_ids == suppression_pair:
            return suppression
    return None


def _rejection(
    suppression: PreviousSuppression,
    status: RagAccountingStatus,
) -> FinalReopenRejection:
    return FinalReopenRejection(
        suppression.candidate_id,
        suppression.parent_candidate_id,
        suppression.same_anomaly_group_id,
        _REOPEN_REASON,
        status,
    )


def _parent_child(
    left: AnomalyCandidate,
    right: AnomalyCandidate,
) -> tuple[AnomalyCandidate, AnomalyCandidate]:
    first, second = sorted(
        (left, right),
        key=lambda candidate: (-candidate.bbox.area, candidate.candidate_id),
    )
    return first, second


def _pairs(
    candidates: tuple[AnomalyCandidate, ...],
) -> tuple[tuple[AnomalyCandidate, AnomalyCandidate], ...]:
    return tuple(
        (left, right)
        for index, left in enumerate(candidates)
        for right in candidates[index + 1 :]
    )


def _concept_match(left: AnomalyCandidate, right: AnomalyCandidate) -> bool:
    return _structured_compatibility(
        left.evidence.relation_authority_input,
        right.evidence.relation_authority_input,
        "concept_family_compatible",
        left.evidence.concept_family == right.evidence.concept_family,
    )


def _descriptor_match(left: AnomalyCandidate, right: AnomalyCandidate) -> bool:
    left_tokens = frozenset(left.evidence.descriptor_tokens)
    right_tokens = frozenset(right.evidence.descriptor_tokens)
    fallback = bool(
        left_tokens and right_tokens and left_tokens.intersection(right_tokens)
    )
    return _structured_compatibility(
        left.evidence.relation_authority_input,
        right.evidence.relation_authority_input,
        "descriptor_compatible",
        fallback,
    )


def _structured_compatibility(
    left: RelationAuthorityInput | None,
    right: RelationAuthorityInput | None,
    field_name: str,
    fallback: bool,
) -> bool:
    inputs = tuple(value for value in (left, right) if value is not None)
    if not inputs:
        return fallback
    return all(getattr(value, field_name) for value in inputs)


def _reasons(
    parent: AnomalyCandidate,
    child: AnomalyCandidate,
    relation_class: RelationClass,
) -> tuple[str, ...]:
    return (
        relation_class.value,
        f"iou={iou(parent.bbox, child.bbox):.3f}",
        f"containment={containment(child.bbox, parent.bbox):.3f}",
    )
