"""JSON serialization for anomaly grouping result artifacts."""

from __future__ import annotations

from modules.anomaly_grouping.models import (
    ANOMALY_GROUPING_RESULT_SCHEMA,
    AnomalyGroupingResult,
)
from modules.anomaly_grouping.shared_contracts import relation_outcome_payload

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject = dict[str, JsonValue]


def result_payload(result: AnomalyGroupingResult) -> JsonObject:
    """Render a result into the stable JSON artifact schema."""
    return {
        "candidate_results": [
            {
                "candidate_id": str(candidate_id),
                "inherited_parent_candidate_id": relation.inherited_parent_candidate_id,
                "kept": relation.kept,
                "relation_group_id": relation.relation_group_id,
            }
            for candidate_id, relation in sorted(
                result.relation_merge.candidate_results.items()
            )
        ],
        "final_reopen_rejections": [
            {
                "candidate_id": str(rejection.candidate_id),
                "previous_parent_candidate_id": str(
                    rejection.previous_parent_candidate_id
                ),
                "previous_same_anomaly_group_id": (
                    rejection.previous_same_anomaly_group_id
                ),
                "reason_code": rejection.reason_code,
                "status": rejection.status.value,
            }
            for rejection in result.relation_merge.final_reopen_rejections
        ],
        "followup_parent_targets": [
            {
                "candidate_id": str(target.candidate_id),
                "selector_id": target.selector_id,
                "selector_type": target.selector_type,
            }
            for target in result.followup_parent_targets
        ],
        "phase": result.phase.value,
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
        "reopen_events": [
            {
                "candidate_id": str(event.candidate_id),
                "event_id": event.event_id,
                "previous_parent_candidate_id": str(event.previous_parent_candidate_id),
                "previous_same_anomaly_group_id": event.previous_same_anomaly_group_id,
                "reason_code": event.reason_code,
                "status": event.status.value,
            }
            for event in result.relation_merge.reopen_events
        ],
        "same_anomaly_groups": [
            {
                "group_id": group.group_id,
                "member_candidate_ids": [
                    str(candidate_id) for candidate_id in group.member_candidate_ids
                ],
                "parent_candidate_id": str(group.parent_candidate_id),
                "suppressed_candidate_ids": [
                    str(candidate_id) for candidate_id in group.suppressed_candidate_ids
                ],
            }
            for group in result.pre_rag.same_anomaly_groups
        ],
        "schema": ANOMALY_GROUPING_RESULT_SCHEMA,
    }
