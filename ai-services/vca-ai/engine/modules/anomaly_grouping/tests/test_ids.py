from __future__ import annotations

from modules.anomaly_grouping import RelationClass
from modules.anomaly_grouping.ids import merged_mask_filename, relation_group_id
from modules.shared import CandidateId


def test_stable_ids_do_not_depend_on_input_order() -> None:
    # Given: the same logical relation arrives with different member ordering.
    left_members = (CandidateId("candidate-b"), CandidateId("candidate-a"))
    right_members = tuple(reversed(left_members))

    # When: stable identifiers are generated from canonical components.
    left_relation = relation_group_id(
        RelationClass.SAME_ANOMALY_DUPLICATE,
        CandidateId("candidate-a"),
        CandidateId("candidate-b"),
        left_members,
    )
    right_relation = relation_group_id(
        RelationClass.SAME_ANOMALY_DUPLICATE,
        CandidateId("candidate-a"),
        CandidateId("candidate-b"),
        right_members,
    )
    left_mask = merged_mask_filename(CandidateId("candidate-a"), left_members)
    right_mask = merged_mask_filename(CandidateId("candidate-a"), right_members)

    # Then: canonical hashing keeps IDs stable regardless of ordering.
    assert left_relation == right_relation
    assert left_mask == right_mask
    assert left_relation.startswith("relation-")
    assert len(left_relation.removeprefix("relation-")) == 64
    assert left_mask.startswith("merged-")
    assert left_mask.endswith(".png")
