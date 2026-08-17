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


# relations.py의 _relation_group에서 관계 판정 결과(RelationGroup)마다 안정적인
# ID를 부여할 때 사용한다.
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


# relation_results.py에서 병합 그룹의 union 마스크 PNG 파일명을 만들 때
# 사용한다. 같은 대표(root)/구성원 조합이면 재실행해도 같은 파일명이 나오도록
# 해시 기반으로 만든다.
def merged_mask_filename(
    root_candidate_id: CandidateId,
    member_candidate_ids: Sequence[CandidateId],
) -> str:
    """Return the stable filename for one merge group's union mask PNG."""
    payload = {
        "members": sorted(str(candidate_id) for candidate_id in member_candidate_ids),
        "root": str(root_candidate_id),
        "schema": _SCHEMA_VERSION,
    }
    return f"merged-{_digest(payload)}.png"


# 위 ID 생성 함수들이 공통으로 쓰는 내부 헬퍼: payload를 정규화된 JSON으로
# 직렬화한 뒤 sha256 해시를 낸다.
def _digest(payload: Mapping[str, str | list[str]]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
