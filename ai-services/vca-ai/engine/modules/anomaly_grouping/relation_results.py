"""Candidate keep/suppress results for relation authority."""

from __future__ import annotations

from typing import TYPE_CHECKING

from modules.anomaly_grouping.models import (
    CandidateRelationResult,
    RelationClass,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from modules.anomaly_grouping.models import AnomalyCandidate, RelationGroup
    from modules.shared import CandidateId


def candidate_results(
    candidates: tuple[AnomalyCandidate, ...],
    relation_groups: tuple[RelationGroup, ...],
) -> dict[CandidateId, CandidateRelationResult]:
    """Resolve per-candidate kept state with canonical same-anomaly parents."""
    results = {
        candidate.candidate_id: CandidateRelationResult(
            candidate.candidate_id,
            kept=True,
        )
        for candidate in candidates
    }
    parent_links: dict[CandidateId, CandidateId] = {}
    relation_ids: dict[CandidateId, str] = {}
    for relation in relation_groups:
        match relation.relation_class:
            case (
                RelationClass.SAME_ANOMALY_DUPLICATE
                | RelationClass.SAME_ANOMALY_REFINEMENT
            ):
                if relation.child_candidate_id not in parent_links:
                    parent_links[relation.child_candidate_id] = (
                        relation.parent_candidate_id
                    )
                    relation_ids[relation.child_candidate_id] = (
                        relation.relation_group_id
                    )
            case (
                RelationClass.CO_LOCATED_DISTINCT_ANOMALY
                | RelationClass.CONTEXT_CONTAINS
            ):
                pass
    for child_candidate_id, parent_candidate_id in parent_links.items():
        canonical_parent_id = _canonical_parent_id(parent_candidate_id, parent_links)
        relation_group_id = relation_ids[child_candidate_id]
        results[child_candidate_id] = CandidateRelationResult(
            candidate_id=child_candidate_id,
            kept=False,
            inherited_parent_candidate_id=canonical_parent_id,
            relation_group_id=relation_group_id,
        )
    return results


def _canonical_parent_id(
    candidate_id: CandidateId,
    parent_links: Mapping[CandidateId, CandidateId],
) -> CandidateId:
    current_id = candidate_id
    visited: set[CandidateId] = set()
    while current_id in parent_links and current_id not in visited:
        visited.add(current_id)
        current_id = parent_links[current_id]
    return current_id
