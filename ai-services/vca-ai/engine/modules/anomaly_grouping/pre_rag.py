"""Pre-RAG same-anomaly grouping."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from modules.anomaly_grouping.geometry import overlaps
from modules.anomaly_grouping.ids import same_anomaly_group_id
from modules.anomaly_grouping.models import (
    AnomalyCandidate,
    PreRagGroupingResult,
    SameAnomalyGroup,
)
from modules.shared import ContractValidationError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from modules.shared import CandidateId


def group_pre_rag_candidates(
    candidates: Sequence[AnomalyCandidate],
    seed_lane_priority: Sequence[str],
) -> PreRagGroupingResult:
    """Group candidates using only allowed pre-RAG attributes."""
    priority = {lane: index for index, lane in enumerate(seed_lane_priority)}
    groups: list[SameAnomalyGroup] = []
    standalone_ids: list[CandidateId] = []
    for bucket in _pre_rag_buckets(candidates).values():
        for component in _overlap_components(bucket):
            if len(component) == 1:
                standalone_ids.append(component[0].candidate_id)
                continue
            parent_id = _select_parent(component, priority)
            members = tuple(sorted(candidate.candidate_id for candidate in component))
            suppressed = tuple(
                candidate_id for candidate_id in members if candidate_id != parent_id
            )
            groups.append(
                SameAnomalyGroup(
                    same_anomaly_group_id(parent_id, members),
                    parent_id,
                    members,
                    suppressed,
                )
            )
    return PreRagGroupingResult(
        tuple(sorted(groups, key=lambda group: group.group_id)),
        tuple(sorted(standalone_ids)),
    )


def _pre_rag_buckets(
    candidates: Sequence[AnomalyCandidate],
) -> Mapping[tuple[str, str, str], tuple[AnomalyCandidate, ...]]:
    buckets: defaultdict[tuple[str, str, str], list[AnomalyCandidate]] = defaultdict(
        list
    )
    for candidate in candidates:
        key = (
            candidate.source_object_id,
            candidate.source_view_id,
            candidate.seed_prompt,
        )
        buckets[key].append(candidate)
    return {key: tuple(value) for key, value in buckets.items()}


def _overlap_components(
    candidates: Sequence[AnomalyCandidate],
) -> tuple[tuple[AnomalyCandidate, ...], ...]:
    remaining = {candidate.candidate_id for candidate in candidates}
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    components: list[tuple[AnomalyCandidate, ...]] = []
    for candidate in sorted(candidates, key=lambda item: item.candidate_id):
        if candidate.candidate_id not in remaining:
            continue
        stack = [candidate.candidate_id]
        member_ids: list[CandidateId] = []
        while stack:
            current_id = stack.pop()
            if current_id not in remaining:
                continue
            remaining.remove(current_id)
            member_ids.append(current_id)
            current = by_id[current_id]
            stack.extend(
                other.candidate_id
                for other in candidates
                if other.candidate_id in remaining
                and overlaps(current.bbox, other.bbox)
            )
        components.append(
            tuple(by_id[candidate_id] for candidate_id in sorted(member_ids))
        )
    return tuple(components)


def _select_parent(
    candidates: Sequence[AnomalyCandidate],
    seed_lane_priority: Mapping[str, int],
) -> CandidateId:
    member_ids = frozenset(candidate.candidate_id for candidate in candidates)
    explicit_parent_ids = tuple(
        candidate.explicit_pre_rag_parent_id
        for candidate in candidates
        if candidate.explicit_pre_rag_parent_id is not None
        and candidate.explicit_pre_rag_parent_id in member_ids
    )
    if explicit_parent_ids:
        explicit_parents = frozenset(explicit_parent_ids)
        if len(explicit_parents) > 1:
            field_name = "explicit_pre_rag_parent_id"
            reason = "conflicting explicit parents in same pre-RAG overlap component"
            raise ContractValidationError(
                field_name,
                reason,
            )
        return next(iter(explicit_parents))
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            -candidate.bbox.area,
            seed_lane_priority.get(candidate.seed_lane, len(seed_lane_priority)),
            candidate.candidate_id,
        ),
    )
    return ranked[0].candidate_id
