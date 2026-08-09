"""JSON serialization for anomaly grouping result artifacts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from modules.anomaly_grouping.models import (
    ANOMALY_GROUPING_RESULT_SCHEMA,
    AnomalyGroupingResult,
)
from modules.anomaly_grouping.shared_contracts import relation_outcome_payload

if TYPE_CHECKING:
    from modules.anomaly_grouping.models import CandidateRelationResult

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject = dict[str, JsonValue]


# io.py의 write_result가 호출한다. AnomalyGroupingResult를 안정적인 JSON 산출물
# 스키마(anomaly_grouping_result_v1)로 직렬화한다.
def result_payload(result: AnomalyGroupingResult) -> JsonObject:
    """Render a result into the stable JSON artifact schema."""
    return {
        "candidate_results": [
            {
                "candidate_id": str(candidate_id),
                "kept": relation.kept,
                "mask": _mask_payload(relation),
                "bbox": _bbox_payload(relation),
                "polygon": _polygon_payload(relation),
                "inherited_parent_candidate_id": (
                    str(relation.inherited_parent_candidate_id)
                    if relation.inherited_parent_candidate_id is not None
                    else None
                ),
                "relation_group_id": relation.relation_group_id,
            }
            for candidate_id, relation in sorted(
                result.relation_merge.candidate_results.items()
            )
        ],
        "followup_parent_targets": [
            {
                "candidate_id": str(target.candidate_id),
                "selector_id": target.selector_id,
                "selector_type": target.selector_type,
            }
            for target in result.followup_parent_targets
        ],
        "relation_groups": [
            {
                "child_candidate_id": str(group.child_candidate_id),
                "parent_candidate_id": str(group.parent_candidate_id),
                "relation_class": group.relation_class.value,
                "relation_authority_outcomes": [
                    relation_outcome_payload(outcome)
                    for outcome in group.relation_authority_outcomes
                ],
                "relation_group_id": group.relation_group_id,
                "reasons": list(group.reasons),
                "source_candidate_ids": [
                    str(candidate_id) for candidate_id in group.source_candidate_ids
                ],
            }
            for group in result.relation_merge.relation_groups
        ],
        "schema": ANOMALY_GROUPING_RESULT_SCHEMA,
    }


def _mask_payload(relation: CandidateRelationResult) -> JsonObject | None:
    if relation.mask is None:
        return None
    return {"path": relation.mask.path, "sha256": relation.mask.sha256}


def _bbox_payload(relation: CandidateRelationResult) -> JsonObject | None:
    if relation.bbox is None:
        return None
    return {
        "x_min": relation.bbox.x_min,
        "y_min": relation.bbox.y_min,
        "x_max": relation.bbox.x_max,
        "y_max": relation.bbox.y_max,
    }


def _polygon_payload(relation: CandidateRelationResult) -> list[JsonValue] | None:
    if relation.polygon is None:
        return None
    return [[point[0], point[1]] for point in relation.polygon]
