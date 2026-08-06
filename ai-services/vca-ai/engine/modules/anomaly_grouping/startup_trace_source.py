"""Report trace-source payloads for anomaly grouping startup output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from modules.anomaly_grouping.startup_json import strings, unique_strings
from modules.report_generating.models import TRACE_SOURCE_SCHEMA

if TYPE_CHECKING:
    from modules.anomaly_grouping.models import (
        AnomalyCandidate,
        AnomalyGroupingResult,
        CandidateEvidence,
    )
    from modules.report_generating.models import JsonObject, JsonValue


@dataclass(frozen=True, slots=True)
class StartupCandidate:
    """Candidate plus startup-only report context for trace-source handoff."""

    candidate: AnomalyCandidate
    image_id: str
    cards: tuple[JsonObject, ...]


def startup_trace_source_payload(
    stage_candidates: tuple[StartupCandidate, ...],
    result: AnomalyGroupingResult,
) -> JsonObject:
    """Build the report trace-source handoff from anomaly grouping output."""
    candidate_payloads: list[JsonValue] = [
        _trace_candidate_payload(item, result) for item in stage_candidates
    ]
    image_payloads: list[JsonValue] = _image_payloads(stage_candidates)
    relation_ids: list[JsonValue] = [
        group.relation_group_id for group in result.relation_merge.relation_groups
    ]
    relation_results = tuple(result.relation_merge.candidate_results.values())
    final_success = bool(relation_results) and all(
        relation.kept for relation in relation_results
    )
    return {
        "budget": {"model_invocations": 0, "prompt_variants": 0, "sam2_calls": 0},
        "candidates": candidate_payloads,
        "images": image_payloads,
        "no_fake_claim_audit": {
            "claimed_candidate_count": len(stage_candidates),
            "fabricated_candidate_count": 0,
            "runner_invoked": True,
            "status": "pass",
        },
        "relations": relation_ids,
        "run_summary": {
            "final_success": final_success,
            "status": "success" if final_success else "incomplete",
        },
        "schema": TRACE_SOURCE_SCHEMA,
    }


def _trace_candidate_payload(
    item: StartupCandidate,
    result: AnomalyGroupingResult,
) -> JsonObject:
    candidate = item.candidate
    candidate_id = str(candidate.candidate_id)
    relation = result.relation_merge.candidate_results[candidate.candidate_id]
    target_type, target_id = _selected_target(candidate_id, result)
    citation_payloads: list[JsonValue] = _citation_payloads(item.cards)
    return {
        "candidate_id": candidate_id,
        "citations": citation_payloads,
        "concept_family": candidate.evidence.concept_family,
        "duplicate_suppression_key": candidate.duplicate_suppression_key or "none",
        "final_success": relation.kept,
        "followup_mode": "automatic",
        "followup_reason": "startup_anomaly_grouping",
        "hybrid_descriptor": _hybrid_descriptor(candidate.evidence),
        "image_id": item.image_id,
        "relation_authority_outcome": _relation_outcome(candidate_id, result),
        "selected_parent_target_id": target_id,
        "selected_parent_target_type": target_type,
        "terminal_status": "kept" if relation.kept else "suppressed",
        "trigger_priority": candidate.seed_lane,
    }


def _selected_target(
    candidate_id: str,
    result: AnomalyGroupingResult,
) -> tuple[str, str]:
    for target in result.followup_parent_targets:
        if str(target.candidate_id) == candidate_id:
            return target.selector_type, target.selector_id
    return "candidate_id", candidate_id


def _relation_outcome(candidate_id: str, result: AnomalyGroupingResult) -> str:
    for group in result.relation_merge.relation_groups:
        source_ids = {str(source_id) for source_id in group.source_candidate_ids}
        if candidate_id in source_ids:
            return group.relation_class.value
    return "no_relation"


def _image_payloads(stage_candidates: tuple[StartupCandidate, ...]) -> list[JsonValue]:
    image_ids = tuple(dict.fromkeys(item.image_id for item in stage_candidates))
    return [
        {"image_id": image_id, "summary": f"startup source image {image_id}"}
        for image_id in image_ids
    ]


def _citation_payloads(cards: tuple[JsonObject, ...]) -> list[JsonValue]:
    citation_ids = unique_strings(
        citation_id
        for card in cards
        for citation_id in strings(card, "source_citation_ids")
    )
    return [
        {
            "citation_id": citation_id,
            "status": "non_exportable_corpus_citation",
        }
        for citation_id in citation_ids
    ]


def _hybrid_descriptor(evidence: CandidateEvidence) -> str:
    tokens = " ".join(evidence.descriptor_tokens).strip()
    return tokens or evidence.concept_family
