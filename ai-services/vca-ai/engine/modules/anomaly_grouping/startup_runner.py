"""Project-level startup adapter for anomaly grouping artifacts."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final, Protocol

from modules.anomaly_grouping.io import write_result
from modules.anomaly_grouping.models import (
    AnomalyCandidate,
    AnomalyGroupingRequest,
    BoundingBox,
    CandidateEvidence,
    MergePhase,
)
from modules.anomaly_grouping.pipeline import run_anomaly_grouping
from modules.anomaly_grouping.startup_json import (
    float_value,
    json_object,
    numbers,
    optional_string,
    raise_contract,
    read_jsonl_objects,
    string,
    strings,
    unique_strings,
)
from modules.anomaly_grouping.startup_trace_source import (
    StartupCandidate,
    startup_trace_source_payload,
)
from modules.report_generating.io import parse_trace_source, write_json
from modules.shared import (
    CandidateId,
    ContractValidationError,
    ExitCode,
    PathSafetyError,
    ensure_no_symlink_leaf,
)

if TYPE_CHECKING:
    from modules.orchestration.stage_paths import StagePathMap
    from modules.report_generating.models import JsonObject

_BBOX_COORDINATE_COUNT: Final = 4


class _ProjectStageRequest(Protocol):
    @property
    def paths(self) -> StagePathMap: ...

    @property
    def dry_run(self) -> bool: ...


def run_anomaly_grouping_stage(request: _ProjectStageRequest) -> int:
    """Run anomaly grouping from public mask-refining and RAG startup artifacts."""
    if request.dry_run:
        return int(ExitCode.OK)
    try:
        stage_candidates, seed_lane_priority = _read_startup_inputs(request)
        result = run_anomaly_grouping(
            AnomalyGroupingRequest(
                MergePhase.INITIAL_RELATION_MERGE,
                seed_lane_priority,
                tuple(item.candidate for item in stage_candidates),
                tuple(item.candidate for item in stage_candidates),
            )
        )
        trace_source = startup_trace_source_payload(stage_candidates, result)
        _ = parse_trace_source(trace_source)
        output_root = request.paths.anomaly_grouping
        result_path = ensure_no_symlink_leaf(
            output_root / "anomaly_grouping_result.json",
            "anomaly grouping startup artifact leaf is a symlink",
        )
        trace_path = ensure_no_symlink_leaf(
            output_root / "report_trace_source.json",
            "anomaly grouping startup artifact leaf is a symlink",
        )
        write_result(result_path, result)
        write_json(trace_path, trace_source)
    except (
        ContractValidationError,
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
        PathSafetyError,
    ):
        return int(ExitCode.INCOMPLETE_OR_FAILURE)
    return int(ExitCode.OK)


def _read_startup_inputs(
    request: _ProjectStageRequest,
) -> tuple[tuple[StartupCandidate, ...], tuple[str, ...]]:
    mask_rows = read_jsonl_objects(
        request.paths.mask_refining / "refined_records.jsonl"
    )
    evidence_rows = read_jsonl_objects(
        request.paths.rag / "rag_candidate_evidence.jsonl"
    )
    cards = _cards_by_candidate(
        read_jsonl_objects(request.paths.rag / "rag_visual_concept_cards.jsonl")
    )
    candidates = _stage_candidates(mask_rows, cards)
    if not candidates:
        raise_contract("post_rag_candidates", "must not be empty")
    return candidates, _seed_lane_priority(mask_rows, evidence_rows)


def _stage_candidates(
    mask_rows: tuple[JsonObject, ...],
    cards: dict[str, tuple[JsonObject, ...]],
) -> tuple[StartupCandidate, ...]:
    candidates: list[StartupCandidate] = []
    for row in mask_rows:
        accepted_ids = frozenset(strings(row, "accepted_candidate_ids"))
        rag_parent_candidate_id = string(row, "rag_parent_candidate_id")
        seed_lane = string(row, "detector_lane")
        candidate_cards = cards.get(rag_parent_candidate_id, ())
        for record in _accepted_records(row, accepted_ids):
            candidate_id = string(record, "candidate_id")
            candidates.append(
                StartupCandidate(
                    AnomalyCandidate(
                        CandidateId(candidate_id),
                        string(record, "source_object_id"),
                        string(record, "source_view_id"),
                        seed_lane,
                        string(record, "prompt"),
                        _bbox(record),
                        _candidate_evidence(candidate_cards),
                    ),
                    string(record, "image_id"),
                    candidate_cards,
                )
            )
    return tuple(candidates)


def _accepted_records(
    row: JsonObject,
    accepted_ids: frozenset[str],
) -> tuple[JsonObject, ...]:
    accepted_candidates = row.get("accepted_candidates")
    if not isinstance(accepted_candidates, list):
        raise_contract("accepted_candidates", "must be a list of objects")
    return tuple(
        record
        for item in accepted_candidates
        if (record := json_object(item, "accepted_candidates"))
        and string(record, "candidate_id") in accepted_ids
    )


def _candidate_evidence(cards: tuple[JsonObject, ...]) -> CandidateEvidence:
    if not cards:
        return CandidateEvidence()
    first = cards[0]
    return CandidateEvidence(
        optional_string(first, "concept_family") or "unknown_visual_anomaly",
        unique_strings(
            term
            for card in cards
            for term in (
                *strings(card, "descriptor_terms"),
                *strings(card, "material_terms"),
                *strings(card, "context_terms"),
            )
        ),
        tuple(string(card, "concept_card_id") for card in cards),
        string(first, "provenance_strength"),
        ("rag_visual_concept_card",),
        visual_cue_confidence=max(_visual_cue_confidence(card) for card in cards),
    )


def _cards_by_candidate(
    cards: tuple[JsonObject, ...],
) -> dict[str, tuple[JsonObject, ...]]:
    grouped: dict[str, list[JsonObject]] = {}
    for card in cards:
        key = string(card, "rag_parent_candidate_id")
        grouped.setdefault(key, []).append(card)
    return {key: tuple(value) for key, value in grouped.items()}


def _seed_lane_priority(
    mask_rows: tuple[JsonObject, ...], evidence_rows: tuple[JsonObject, ...]
) -> tuple[str, ...]:
    return unique_strings(
        lane
        for row in (*mask_rows, *evidence_rows)
        for lane in (
            optional_string(row, "detector_lane"),
            optional_string(row, "lane"),
        )
        if lane is not None
    )


def _bbox(record: JsonObject) -> BoundingBox:
    values = numbers(record, "bbox_xyxy")
    if len(values) != _BBOX_COORDINATE_COUNT:
        raise_contract("bbox_xyxy", "must contain four numbers")
    return BoundingBox(*values)


def _visual_cue_confidence(card: JsonObject) -> float:
    return float_value(json_object(card.get("visual_cue"), "visual_cue"), "confidence")
