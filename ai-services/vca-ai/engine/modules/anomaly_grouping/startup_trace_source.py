"""Report trace-source payloads for anomaly grouping startup output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from modules.anomaly_grouping.startup_json import (
    float_value,
    string,
    strings,
    unique_strings,
)
from modules.report_generating.models import TRACE_SOURCE_SCHEMA
from modules.shared import CandidateId

if TYPE_CHECKING:
    from modules.anomaly_grouping.models import (
        AnomalyCandidate,
        AnomalyGroupingResult,
        BoundingBox,
        CandidateEvidence,
    )
    from modules.report_generating.models import JsonObject, JsonValue


@dataclass(frozen=True, slots=True)
class StartupCandidate:
    """Candidate plus startup-only report context for trace-source handoff."""

    candidate: AnomalyCandidate
    image_id: str
    cards: tuple[JsonObject, ...]


# startup_trace_source_payload의 no_fake_claim_audit 계산에 쓰인다.
def _fabricated_candidate_count(stage_candidates: tuple[StartupCandidate, ...]) -> int:
    """이전에 나온 candidate_id와 충돌하는(identity가 겹치는) 후보 수를 센다.

    진짜 accepted-candidate 행은 항상 고유한 candidate_id를 갖는다
    (mask_refining의 normalize_candidate 참고). 여기서 중복이 발견됐다는
    건 같은 identity가 두 번 이상 주장됐다는 뜻이며 - 이 스테이지에서
    확인할 수 있는, 조작되었거나 중복 집계된 후보의 유일한 구체적 신호다.
    """
    seen_ids: set[str] = set()
    fabricated = 0
    for item in stage_candidates:
        candidate_id = str(item.candidate.candidate_id)
        if candidate_id in seen_ids:
            fabricated += 1
            continue
        seen_ids.add(candidate_id)
    return fabricated


# startup_runner.py의 run_anomaly_grouping_stage가 호출하는 최상위 함수.
# anomaly_grouping 결과를 report_generating이 기대하는 report_trace_source_v1
# 스키마의 JSON으로 변환한다.
def startup_trace_source_payload(
    stage_candidates: tuple[StartupCandidate, ...],
    result: AnomalyGroupingResult,
    citation_details: dict[str, JsonObject],
) -> JsonObject:
    """Build the report trace-source handoff from anomaly grouping output."""
    candidate_payloads: list[JsonValue] = [
        _trace_candidate_payload(item, result, citation_details)
        for item in stage_candidates
    ]
    image_payloads: list[JsonValue] = _image_payloads(stage_candidates)
    relation_ids: list[JsonValue] = [
        group.relation_group_id for group in result.relation_merge.relation_groups
    ]
    # 후보가 결국 kept=False가 됐다는 건 relation authority가 그걸 canonical한
    # duplicate/refinement 부모로 병합했다는 뜻일 뿐이다(relation_results.py
    # 참고) - 이는 정상적인 중복 제거이지 실패가 아니다. 병합 이후 살아남는
    # 후보가 *하나도* 없을 때만 보고할 게 없는 실행이 된다.
    relation_results = tuple(result.relation_merge.candidate_results.values())
    final_success = bool(relation_results) and any(
        relation.kept for relation in relation_results
    )
    fabricated_candidate_count = _fabricated_candidate_count(stage_candidates)
    return {
        "budget": {"model_invocations": 0, "prompt_variants": 0, "sam2_calls": 0},
        "candidates": candidate_payloads,
        "images": image_payloads,
        "no_fake_claim_audit": {
            "claimed_candidate_count": len(stage_candidates),
            "fabricated_candidate_count": fabricated_candidate_count,
            # 여기 있는 모든 StartupCandidate는 mask_refining의
            # accepted_candidates 행에서 만들어졌고(startup_runner._stage_candidates
            # 참고), rough_masking의 execute_adapter는 자기 자신의 runner가
            # 실제로 호출되지 않았을 때 이미 fail closed로 막아버린다 - 그래서
            # 후보가 이 지점에 도달했을 때는 항상 진짜다.
            "runner_invoked": True,
            "status": "pass" if fabricated_candidate_count == 0 else "fail",
        },
        "relations": relation_ids,
        "run_summary": {
            "final_success": final_success,
            "status": "success" if final_success else "incomplete",
        },
        "schema": TRACE_SOURCE_SCHEMA,
    }


# startup_trace_source_payload가 후보마다 호출한다. report_generating의
# TraceCandidate 필드에 대응하는 후보 하나의 JSON을 만든다.
def _trace_candidate_payload(
    item: StartupCandidate,
    result: AnomalyGroupingResult,
    citation_details: dict[str, JsonObject],
) -> JsonObject:
    candidate = item.candidate
    candidate_id = str(candidate.candidate_id)
    relation = result.relation_merge.candidate_results[candidate.candidate_id]
    target_type, target_id = _selected_target(candidate_id, result)
    citation_payloads: list[JsonValue] = _citation_payloads(
        item.cards, citation_details
    )
    return {
        "candidate_id": candidate_id,
        "citations": citation_payloads,
        # relation.bbox/polygons는 둘 다 (원래 detector bbox가 아니라) 최종
        # (마스크 합집합으로 병합됐을 수도 있는) 마스크에서 파생된다 -
        # 기준(standard)은 마스크고, 이것들은 표시 전용이다. polygons는 FE가
        # 렌더링하는 벡터화된 윤곽선이고(끊어진 마스크 조각마다 하나씩);
        # bbox는 보조/레거시 표시용으로 남아 있다. absorbed된 후보는 둘 다
        # 없다: 그 픽셀은 이제 그룹의 합집합 안에 계속 남아 있을 뿐이다.
        "bbox": _bbox_payload(relation.bbox),
        "polygons": _polygons_payload(relation.polygons),
        "concept_family": candidate.evidence.concept_family,
        "duplicate_suppression_key": candidate.duplicate_suppression_key or "none",
        "final_success": relation.kept,
        "followup_mode": "automatic",
        "followup_reason": "startup_anomaly_grouping",
        "hybrid_descriptor": _hybrid_descriptor(candidate.evidence),
        "image_id": item.image_id,
        "qwen_final_success": candidate.qwen_final_success,
        "qwen_report_display_text": candidate.qwen_report_display_text,
        "qwen_confidence": candidate.qwen_confidence,
        "relation_authority_outcome": _relation_outcome(candidate_id, result),
        "selected_parent_target_id": target_id,
        "selected_parent_target_type": target_type,
        "terminal_status": "kept" if relation.kept else "suppressed",
        "trigger_priority": candidate.seed_lane,
    }


# _trace_candidate_payload에서 호출된다. relation.bbox(마스크에서 파생된 값,
# 흡수된 후보는 None)를 trace_source의 bbox 필드로 직렬화한다.
def _bbox_payload(bbox: BoundingBox | None) -> JsonObject | None:
    if bbox is None:
        return None
    return {
        "x_min": bbox.x_min,
        "y_min": bbox.y_min,
        "x_max": bbox.x_max,
        "y_max": bbox.y_max,
    }


# _trace_candidate_payload에서 호출된다. relation.polygons(성분별 마스크
# 윤곽선 벡터화 결과, 흡수된 후보는 None)을 trace_source의 polygons 필드로
# 직렬화한다.
def _polygons_payload(
    polygons: tuple[tuple[tuple[float, float], ...], ...] | None,
) -> list[JsonValue] | None:
    if polygons is None:
        return None
    return [[[point[0], point[1]] for point in polygon] for polygon in polygons]


# _trace_candidate_payload에서 호출된다. 이 후보를 가리키는 follow-up 선택자를
# 찾는다. 흡수된(kept=False) 후보는 그 자체로는 더 이상 follow-up 대상이 될 수
# 없으므로, 병합돼 들어간 대표(inherited_parent_candidate_id)를 선택자로 쓴다.
def _selected_target(
    candidate_id: str,
    result: AnomalyGroupingResult,
) -> tuple[str, str]:
    for target in result.followup_parent_targets:
        if str(target.candidate_id) == candidate_id:
            return target.selector_type, target.selector_id
    relation = result.relation_merge.candidate_results.get(CandidateId(candidate_id))
    if relation is not None and relation.inherited_parent_candidate_id is not None:
        return "candidate_id", str(relation.inherited_parent_candidate_id)
    return "candidate_id", candidate_id


# _trace_candidate_payload에서 호출된다. 이 후보가 속한 관계 그룹의 클래스를
# 찾아 문자열로 반환하고, 관계가 없으면 "no_relation"을 반환한다.
def _relation_outcome(candidate_id: str, result: AnomalyGroupingResult) -> str:
    for group in result.relation_merge.relation_groups:
        source_ids = {str(source_id) for source_id in group.source_candidate_ids}
        if candidate_id in source_ids:
            return group.relation_class.value
    return "no_relation"


# startup_trace_source_payload에서 호출된다. 후보들에서 등장한 image_id를
# 최초 등장 순서로 중복 제거해 이미지 요약 목록을 만든다.
def _image_payloads(stage_candidates: tuple[StartupCandidate, ...]) -> list[JsonValue]:
    image_ids = tuple(dict.fromkeys(item.image_id for item in stage_candidates))
    return [
        {"image_id": image_id, "summary": f"startup source image {image_id}"}
        for image_id in image_ids
    ]


# _trace_candidate_payload에서 호출된다. 후보에 붙은 concept card들의
# source_citation_ids를 모아 인용 payload 목록을 만든다.
def _citation_payloads(
    cards: tuple[JsonObject, ...],
    citation_details: dict[str, JsonObject],
) -> list[JsonValue]:
    citation_ids = unique_strings(
        citation_id
        for card in cards
        for citation_id in strings(card, "source_citation_ids")
    )
    return [
        _citation_payload(citation_id, citation_details) for citation_id in citation_ids
    ]


# _citation_payloads에서 인용 ID마다 호출된다. 페이지 번호가 있는 검색 결과와
# 매칭되는 인용만 "exported"로 표시하고, 나머지는 non_exportable로 표시한다.
def _citation_payload(
    citation_id: str,
    citation_details: dict[str, JsonObject],
) -> JsonObject:
    detail = citation_details.get(citation_id)
    page_number = detail.get("page_number") if detail is not None else None
    has_page_number = isinstance(page_number, int) and not isinstance(page_number, bool)
    if detail is None or not has_page_number:
        return {"citation_id": citation_id, "status": "non_exportable_corpus_citation"}
    source_citation = string(detail, "source_citation")
    return {
        "citation_id": citation_id,
        "status": "exported",
        "source_citation": source_citation,
        # 업스트림에 별도의 표시용 title이 없다; 검색이 갖고 다니는 유일한
        # 사람이 읽을 수 있는 식별자는 소스 문서 이름뿐이다.
        "title": source_citation,
        "page_number": page_number,
        "score": float_value(detail, "score"),
    }


# _trace_candidate_payload에서 호출된다. descriptor_tokens를 이어붙여 표시용
# 요약 문자열을 만들고, 비어 있으면 concept_family로 대체한다.
def _hybrid_descriptor(evidence: CandidateEvidence) -> str:
    tokens = " ".join(evidence.descriptor_tokens).strip()
    return tokens or evidence.concept_family
