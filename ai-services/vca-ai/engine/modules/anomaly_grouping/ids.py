"""Stable identifiers for anomaly grouping artifacts."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from modules.anomaly_grouping.models import RelationClass
    from modules.shared import CandidateId

_SCHEMA_VERSION: Final = "anomaly_grouping_ids_v1"


def same_anomaly_group_id(
    parent_candidate_id: CandidateId,
    member_candidate_ids: Sequence[CandidateId],
) -> str:
    """Return the stable same-anomaly group ID for follow-up selection."""
    payload = {
        "members": sorted(str(candidate_id) for candidate_id in member_candidate_ids),
        "parent": str(parent_candidate_id),
        "relation_family": "same_anomaly",
        "schema": _SCHEMA_VERSION,
    }
    return f"same-anomaly-{_digest(payload)}"


def relation_group_id(
    relation_class: RelationClass,
    parent_candidate_id: CandidateId,
    child_candidate_id: CandidateId,
    source_candidate_ids: Sequence[CandidateId],
) -> str:
    """Return the stable relation group ID for a candidate pair."""
    payload = {
        "child": str(child_candidate_id),
        "parent": str(parent_candidate_id),
        "relation_class": relation_class.value,
        "schema": _SCHEMA_VERSION,
        "sources": sorted(str(candidate_id) for candidate_id in source_candidate_ids),
    }
    return f"relation-{_digest(payload)}"


def reopen_event_id(
    candidate_id: CandidateId,
    previous_parent_candidate_id: CandidateId,
    previous_same_anomaly_group_id: str,
    reason_code: str,
) -> str:
    """Return the stable ID for one bounded reopened-RAG event."""
    payload = {
        "candidate": str(candidate_id),
        "previous_group": previous_same_anomaly_group_id,
        "previous_parent": str(previous_parent_candidate_id),
        "reason": reason_code,
        "schema": _SCHEMA_VERSION,
    }
    return f"reopen-{_digest(payload)}"


def _digest(payload: Mapping[str, str | list[str]]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
