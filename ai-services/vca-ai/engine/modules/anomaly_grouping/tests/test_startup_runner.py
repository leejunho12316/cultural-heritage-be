from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeGuard

from modules.anomaly_grouping.models import (
    AnomalyCandidate,
    AnomalyGroupingResult,
    BoundingBox,
    CandidateRelationResult,
    RelationMergeResult,
)
from modules.anomaly_grouping.startup_runner import run_anomaly_grouping_stage
from modules.anomaly_grouping.startup_trace_source import (
    StartupCandidate,
    startup_trace_source_payload,
)
from modules.anomaly_grouping.tests.mask_test_support import rect_mask
from modules.orchestration.stage_paths import stage_paths
from modules.report_generating.json_parser import parse_json_object
from modules.shared import CandidateId, ExitCode

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from modules.anomaly_grouping.models import MaskReference
    from modules.orchestration.stage_paths import StagePathMap
    from modules.report_generating.models import JsonObject, JsonValue


@dataclass(frozen=True, slots=True)
class _StageRequest:
    paths: StagePathMap
    dry_run: bool


def _is_json_objects(value: JsonValue) -> TypeGuard[list[JsonObject]]:
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)


def test_run_anomaly_grouping_stage_dry_run_writes_no_real_outputs(
    tmp_path: Path,
) -> None:
    # Given: a dry-run anomaly grouping request with no post-mask artifacts.
    request = _request(tmp_path, dry_run=True)

    # When: the public startup adapter executes.
    exit_code = _run_anomaly_grouping_stage(request)

    # Then: startup can continue without creating real downstream artifacts.
    assert exit_code == int(ExitCode.OK)
    assert not (
        request.paths.anomaly_grouping / "anomaly_grouping_result.json"
    ).exists()
    assert not (request.paths.anomaly_grouping / "report_trace_source.json").exists()


def test_run_anomaly_grouping_stage_returns_two_when_inputs_are_missing(
    tmp_path: Path,
) -> None:
    # Given: startup reaches anomaly grouping before mask-refining outputs exist.
    request = _request(tmp_path)

    # When: the public startup adapter executes.
    exit_code = _run_anomaly_grouping_stage(request)

    # Then: missing required upstream inputs fail closed without real outputs.
    assert exit_code == int(ExitCode.INCOMPLETE_OR_FAILURE)
    assert not (
        request.paths.anomaly_grouping / "anomaly_grouping_result.json"
    ).exists()
    assert not (request.paths.anomaly_grouping / "report_trace_source.json").exists()


def test_run_anomaly_grouping_stage_writes_result_and_report_trace_source(
    tmp_path: Path,
) -> None:
    # Given: public RAG and mask-refining artifacts describe one post-mask candidate.
    request = _request(tmp_path)
    _write_upstream_inputs(tmp_path, request.paths)

    # When: the public startup adapter executes.
    exit_code = _run_anomaly_grouping_stage(request)

    # Then: anomaly grouping writes its result and report handoff under its root.
    assert exit_code == int(ExitCode.OK)
    result = _read_json(request.paths.anomaly_grouping / "anomaly_grouping_result.json")
    trace_source = _read_json(
        request.paths.anomaly_grouping / "report_trace_source.json"
    )
    assert result["schema"] == "anomaly_grouping_result_v1"
    assert trace_source["schema"] == "report_trace_source_v1"
    audit = trace_source["no_fake_claim_audit"]
    assert isinstance(audit, dict)
    assert audit["runner_invoked"] is True
    assert audit["fabricated_candidate_count"] == 0
    candidates = trace_source["candidates"]
    assert _is_json_objects(candidates)
    assert candidates[0]["candidate_id"] == "refined-candidate-001"
    assert candidates[0]["concept_family"] == "deposit"
    assert candidates[0]["hybrid_descriptor"] == "white powdery stone surface"
    assert candidates[0]["citations"] == [
        {
            "citation_id": "citation-001",
            "status": "non_exportable_corpus_citation",
        }
    ]


def test_run_anomaly_grouping_stage_includes_passthrough_candidates(
    tmp_path: Path,
) -> None:
    # Given: the usual refined candidate, plus a second candidate mask_refining
    # could not refine at all because RAG found zero citable evidence for it -
    # mask_refining still passes it through with its own rough mask restored.
    request = _request(tmp_path)
    _write_upstream_inputs(tmp_path, request.paths)
    passthrough_mask = rect_mask(
        tmp_path, "passthrough-candidate-001", BoundingBox(5.0, 5.0, 25.0, 25.0)
    )
    _write_object_mask(request.paths, "image-001", "object-002")
    _write_jsonl(
        request.paths.mask_refining / "passthrough_records.jsonl",
        (
            {
                "rag_parent_candidate_id": "passthrough-candidate-001",
                "detector_lane": "owlv2_sam2",
                "diagnostics": ["no_rag_evidence_passthrough"],
                "accepted_candidate_ids": ["passthrough-candidate-001"],
                "accepted_candidates": [
                    {
                        "candidate_id": "passthrough-candidate-001",
                        "image_id": "image-001",
                        "source_object_id": "object-002",
                        "source_view_id": "view-002",
                        "bbox_xyxy": [5.0, 5.0, 25.0, 25.0],
                        "original_bbox_xyxy": [5.0, 5.0, 25.0, 25.0],
                        "prompt": "white surface deposit",
                        "qwen_final_success": False,
                        "qwen_report_display_text": "없음",
                        "qwen_confidence": None,
                        "mask_path": passthrough_mask.path,
                        "mask_sha256": passthrough_mask.sha256,
                    }
                ],
            },
        ),
    )

    # When: the public startup adapter executes.
    exit_code = _run_anomaly_grouping_stage(request)

    # Then: both candidates are reported - RAG evidence missing does not drop
    # the candidate from the run.
    assert exit_code == int(ExitCode.OK)
    trace_source = _read_json(
        request.paths.anomaly_grouping / "report_trace_source.json"
    )
    candidates = trace_source["candidates"]
    assert _is_json_objects(candidates)
    candidate_ids = {c["candidate_id"] for c in candidates}
    assert candidate_ids == {"refined-candidate-001", "passthrough-candidate-001"}
    passthrough_candidate = next(
        c for c in candidates if c["candidate_id"] == "passthrough-candidate-001"
    )
    assert passthrough_candidate["concept_family"] == "unknown"
    assert passthrough_candidate["qwen_final_success"] is False
    assert passthrough_candidate["final_success"] is True


def test_run_anomaly_grouping_stage_merges_duplicate_candidate_id_across_lineages(
    tmp_path: Path,
) -> None:
    # Given: the same real anomaly reaches mask_refining through two distinct
    # rag_parent lineages (eg. a plain object-crop detection and a
    # tile-merged detection of the same physical region). Refining the same
    # region with the same prompt/lane converges to byte-identical output, so
    # both rows end up sharing the same candidate_id - a true duplicate, not
    # fabrication.
    request = _request(tmp_path)
    _write_upstream_inputs(tmp_path, request.paths)
    mask = rect_mask(
        tmp_path, "refined-candidate-001", BoundingBox(0.0, 0.0, 20.0, 20.0)
    )
    second_parent_candidate_id = "tile-merged-parent-001"

    def _row(rag_parent_candidate_id: str, records_json: Path) -> JsonObject:
        return {
            "accepted_candidates": [
                {
                    "bbox_xyxy": [0.0, 0.0, 20.0, 20.0],
                    "original_bbox_xyxy": [0.0, 0.0, 20.0, 20.0],
                    "candidate_id": "refined-candidate-001",
                    "image_id": "image-001",
                    "prompt": "white surface deposit",
                    "source_object_id": "object-001",
                    "source_view_id": "view-001",
                    "qwen_final_success": True,
                    "qwen_report_display_text": "white deposit observed",
                    "qwen_confidence": 0.75,
                    "mask_path": mask.path,
                    "mask_sha256": mask.sha256,
                }
            ],
            "accepted_candidate_ids": ["refined-candidate-001"],
            "detector_lane": "owlv2_sam2",
            "diagnostics": [],
            "model_lane": "owlv2",
            "prompt_texts": ["white surface deposit"],
            "rag_parent_candidate_id": rag_parent_candidate_id,
            "records_json": str(records_json),
            "status": "executed",
        }

    _write_jsonl(
        request.paths.mask_refining / "refined_records.jsonl",
        (
            _row(
                "rough-parent-001",
                request.paths.mask_refining
                / "refined"
                / "refined-candidate-001"
                / "records.json",
            ),
            _row(
                second_parent_candidate_id,
                request.paths.mask_refining
                / "refined"
                / "refined-candidate-001-b"
                / "records.json",
            ),
        ),
    )
    _write_jsonl(
        request.paths.rag / "rag_visual_concept_cards.jsonl",
        (
            {
                "concept_card_id": "card-001",
                "concept_family": "deposit",
                "context_terms": ["surface"],
                "descriptor_terms": ["white", "powdery"],
                "material_terms": ["stone"],
                "provenance_strength": "strong",
                "rag_parent_candidate_id": "rough-parent-001",
                "raw_retrieved_sentence": "white powdery deposit on stone",
                "retrieval_score": 7.5,
                "source_citation_ids": ["citation-001"],
                "visual_cue": {
                    "boundary_relation": "interior",
                    "color_bucket": "white",
                    "confidence": 0.91,
                    "morphology": "crust",
                    "reasons": ["fixture"],
                    "size_class": "local",
                    "texture_proxy": "powdery",
                },
            },
            {
                "concept_card_id": "card-002",
                "concept_family": "deposit",
                "context_terms": ["surface"],
                "descriptor_terms": ["rough"],
                "material_terms": ["stone"],
                "provenance_strength": "strong",
                "rag_parent_candidate_id": second_parent_candidate_id,
                "raw_retrieved_sentence": "rough deposit patch",
                "retrieval_score": 6.0,
                "source_citation_ids": ["citation-002"],
                "visual_cue": {
                    "boundary_relation": "interior",
                    "color_bucket": "white",
                    "confidence": 0.80,
                    "morphology": "crust",
                    "reasons": ["fixture"],
                    "size_class": "local",
                    "texture_proxy": "rough",
                },
            },
        ),
    )

    # When: the public startup adapter executes.
    exit_code = _run_anomaly_grouping_stage(request)

    # Then: the two rows collapse into a single reported candidate with
    # evidence merged from both lineages, and the audit passes.
    assert exit_code == int(ExitCode.OK)
    trace_source = _read_json(
        request.paths.anomaly_grouping / "report_trace_source.json"
    )
    audit = trace_source["no_fake_claim_audit"]
    assert isinstance(audit, dict)
    assert audit["fabricated_candidate_count"] == 0
    assert audit["status"] == "pass"
    candidates = trace_source["candidates"]
    assert _is_json_objects(candidates)
    assert len(candidates) == 1
    assert candidates[0]["candidate_id"] == "refined-candidate-001"
    citation_ids = {c["citation_id"] for c in candidates[0]["citations"]}
    assert citation_ids == {"citation-001", "citation-002"}


def test_run_anomaly_grouping_stage_discards_candidate_outside_object_mask(
    tmp_path: Path,
) -> None:
    # Given: one candidate whose mask lands on its object's silhouette, and
    # a second candidate whose mask lands entirely outside its own object's
    # silhouette - mirroring the real bug where rough_masking's tile-level
    # detection runs on the rectangular bbox crop (not the object silhouette)
    # and can pick up background sitting in a bbox corner.
    request = _request(tmp_path)
    on_object_mask = rect_mask(
        tmp_path, "on-object-candidate", BoundingBox(0.0, 0.0, 20.0, 20.0)
    )
    _write_object_mask(request.paths, "image-001", "object-001")
    off_object_mask = rect_mask(
        tmp_path, "off-object-candidate", BoundingBox(100.0, 100.0, 120.0, 120.0)
    )
    object_mask_dir = (
        request.paths.preprocessing
        / "assets"
        / "objects"
        / "image-001"
        / "object-002"
    )
    object_mask_dir.mkdir(parents=True, exist_ok=True)
    _ = rect_mask(object_mask_dir, "mask", BoundingBox(0.0, 0.0, 10.0, 10.0))

    def _row(
        candidate_id: str, object_id: str, mask: MaskReference, parent_id: str
    ) -> JsonObject:
        return {
            "accepted_candidates": [
                {
                    "bbox_xyxy": [0.0, 0.0, 20.0, 20.0],
                    "original_bbox_xyxy": [0.0, 0.0, 20.0, 20.0],
                    "candidate_id": candidate_id,
                    "image_id": "image-001",
                    "prompt": "white surface deposit",
                    "source_object_id": object_id,
                    "source_view_id": "view-001",
                    "qwen_final_success": True,
                    "qwen_report_display_text": "white deposit observed",
                    "qwen_confidence": 0.75,
                    "mask_path": mask.path,
                    "mask_sha256": mask.sha256,
                }
            ],
            "accepted_candidate_ids": [candidate_id],
            "detector_lane": "owlv2_sam2",
            "diagnostics": [],
            "model_lane": "owlv2",
            "prompt_texts": ["white surface deposit"],
            "rag_parent_candidate_id": parent_id,
            "records_json": str(tmp_path / f"{candidate_id}-records.json"),
            "status": "executed",
        }

    _write_jsonl(
        request.paths.mask_refining / "refined_records.jsonl",
        (
            _row("on-object-candidate", "object-001", on_object_mask, "parent-001"),
            _row("off-object-candidate", "object-002", off_object_mask, "parent-002"),
        ),
    )
    _write_jsonl(request.paths.rag / "prompt_rag_results.jsonl", ())
    _write_jsonl(request.paths.rag / "rag_visual_concept_cards.jsonl", ())

    # When: the public startup adapter executes.
    exit_code = _run_anomaly_grouping_stage(request)

    # Then: only the candidate that actually lands on its object survives.
    assert exit_code == int(ExitCode.OK)
    trace_source = _read_json(
        request.paths.anomaly_grouping / "report_trace_source.json"
    )
    candidates = trace_source["candidates"]
    assert _is_json_objects(candidates)
    candidate_ids = {c["candidate_id"] for c in candidates}
    assert candidate_ids == {"on-object-candidate"}


def test_run_anomaly_grouping_stage_exports_citation_from_rag_retrieval_results(
    tmp_path: Path,
) -> None:
    # Given: the same upstream artifacts, plus a RAG retrieval result that
    # resolves the candidate's cited chunk to a real source/page/score.
    request = _request(tmp_path)
    _write_upstream_inputs(tmp_path, request.paths)
    _write_jsonl(
        request.paths.rag / "prompt_rag_results.jsonl",
        (
            {
                "chunk_id": "chunk-001",
                "citation_id": "citation-001",
                "page_number": 42,
                "score": 0.91,
                "source_citation": "example.pdf",
            },
        ),
    )

    # When: the public startup adapter executes.
    exit_code = _run_anomaly_grouping_stage(request)

    # Then: the trace source carries the resolved citation instead of the
    # permanently-non-exportable stub.
    assert exit_code == int(ExitCode.OK)
    trace_source = _read_json(
        request.paths.anomaly_grouping / "report_trace_source.json"
    )
    candidates = trace_source["candidates"]
    assert _is_json_objects(candidates)
    assert candidates[0]["citations"] == [
        {
            "citation_id": "citation-001",
            "status": "exported",
            "source_citation": "example.pdf",
            "title": "example.pdf",
            "page_number": 42,
            "score": 0.91,
        }
    ]


def test_run_anomaly_grouping_stage_rejects_legacy_image_field(
    tmp_path: Path,
) -> None:
    # Given: accepted_candidates omits the canonical image_id handoff field.
    request = _request(tmp_path)
    _write_upstream_inputs(tmp_path, request.paths, image_field="image")

    # When: the public startup adapter executes.
    exit_code = _run_anomaly_grouping_stage(request)

    # Then: legacy image fallback is rejected before writing real outputs.
    assert exit_code == int(ExitCode.INCOMPLETE_OR_FAILURE)
    assert not (
        request.paths.anomaly_grouping / "anomaly_grouping_result.json"
    ).exists()
    assert not (request.paths.anomaly_grouping / "report_trace_source.json").exists()


def test_run_anomaly_grouping_stage_rejects_result_symlink_leaf(
    tmp_path: Path,
) -> None:
    # Given: the fixed anomaly grouping result leaf is a symlink.
    request = _request(tmp_path)
    _write_upstream_inputs(tmp_path, request.paths)
    external = tmp_path / "external-result.json"
    _ = external.write_text("sentinel", encoding="utf-8")
    request.paths.anomaly_grouping.mkdir(parents=True)
    (request.paths.anomaly_grouping / "anomaly_grouping_result.json").symlink_to(
        external
    )

    # When: the public startup adapter executes.
    exit_code = _run_anomaly_grouping_stage(request)

    # Then: it fails closed without clobbering the symlink target.
    assert exit_code == int(ExitCode.INCOMPLETE_OR_FAILURE)
    assert external.read_text(encoding="utf-8") == "sentinel"


def test_run_anomaly_grouping_stage_rejects_trace_symlink_leaf(
    tmp_path: Path,
) -> None:
    # Given: the fixed report trace-source leaf is a symlink.
    request = _request(tmp_path)
    _write_upstream_inputs(tmp_path, request.paths)
    external = tmp_path / "external-trace.json"
    _ = external.write_text("sentinel", encoding="utf-8")
    request.paths.anomaly_grouping.mkdir(parents=True)
    (request.paths.anomaly_grouping / "report_trace_source.json").symlink_to(
        external
    )

    # When: the public startup adapter executes.
    exit_code = _run_anomaly_grouping_stage(request)

    # Then: it fails closed without clobbering the symlink target.
    assert exit_code == int(ExitCode.INCOMPLETE_OR_FAILURE)
    assert external.read_text(encoding="utf-8") == "sentinel"


def _candidate(
    tmp_path: Path, candidate_id: CandidateId, bbox: BoundingBox
) -> AnomalyCandidate:
    return AnomalyCandidate(
        candidate_id,
        "image-001",
        "object-001",
        "view-001",
        "owlv2_sam2",
        "white surface deposit",
        bbox,
        rect_mask(tmp_path, str(candidate_id), bbox),
    )


def test_trace_source_fails_run_when_all_candidates_suppressed(
    tmp_path: Path,
) -> None:
    # Given: relation authority suppressed the reportable candidate.
    candidate_id = CandidateId("candidate-suppressed")
    candidate = _candidate(tmp_path, candidate_id, BoundingBox(0.0, 0.0, 20.0, 20.0))
    result = AnomalyGroupingResult(
        RelationMergeResult(
            (),
            {candidate_id: CandidateRelationResult(candidate_id, kept=False)},
        ),
        (),
    )

    # When: startup trace-source payload is prepared for report generation.
    payload = startup_trace_source_payload(
        (StartupCandidate(candidate, "image-001", ()),), result, {}
    )

    # Then: candidate-level suppression makes the run summary fail closed.
    candidates = payload["candidates"]
    assert _is_json_objects(candidates)
    assert candidates[0]["final_success"] is False
    run_summary = payload["run_summary"]
    assert isinstance(run_summary, dict)
    assert run_summary["final_success"] is False
    assert run_summary["status"] == "incomplete"


def test_trace_source_fails_run_when_no_candidates_are_reportable() -> None:
    # Given: relation authority produced no kept candidate result for report generation.
    result = AnomalyGroupingResult(RelationMergeResult((), {}), ())

    # When: startup trace-source payload is prepared with no candidates.
    payload = startup_trace_source_payload((), result, {})

    # Then: an empty relation result cannot be reported as a successful run.
    run_summary = payload["run_summary"]
    assert isinstance(run_summary, dict)
    assert run_summary["final_success"] is False
    assert run_summary["status"] == "incomplete"


def test_trace_source_succeeds_when_duplicate_candidate_is_suppressed(
    tmp_path: Path,
) -> None:
    # Given: relation authority merged one duplicate candidate into a kept
    # canonical parent - ordinary dedup, not a failure.
    kept_id = CandidateId("candidate-kept")
    suppressed_id = CandidateId("candidate-suppressed")
    kept_candidate = _candidate(tmp_path, kept_id, BoundingBox(0.0, 0.0, 20.0, 20.0))
    suppressed_candidate = _candidate(
        tmp_path, suppressed_id, BoundingBox(1.0, 1.0, 21.0, 21.0)
    )
    result = AnomalyGroupingResult(
        RelationMergeResult(
            (),
            {
                kept_id: CandidateRelationResult(
                    kept_id,
                    kept=True,
                    mask=kept_candidate.mask,
                    bbox=kept_candidate.bbox,
                ),
                suppressed_id: CandidateRelationResult(
                    suppressed_id,
                    kept=False,
                    inherited_parent_candidate_id=kept_id,
                ),
            },
        ),
        (),
    )

    # When: startup trace-source payload is prepared for report generation.
    payload = startup_trace_source_payload(
        (
            StartupCandidate(kept_candidate, "image-001", ()),
            StartupCandidate(suppressed_candidate, "image-001", ()),
        ),
        result,
        {},
    )

    # Then: at least one reportable candidate survives, so the run succeeds.
    run_summary = payload["run_summary"]
    assert isinstance(run_summary, dict)
    assert run_summary["final_success"] is True
    assert run_summary["status"] == "success"


def test_trace_source_flags_duplicate_candidate_identity_as_fabricated(
    tmp_path: Path,
) -> None:
    # Given: the same candidate identity claimed twice in one stage batch -
    # a real pipeline bug (double-counted row), not a legitimate duplicate.
    candidate_id = CandidateId("candidate-dup")
    candidate = _candidate(tmp_path, candidate_id, BoundingBox(0.0, 0.0, 20.0, 20.0))
    result = AnomalyGroupingResult(
        RelationMergeResult(
            (),
            {
                candidate_id: CandidateRelationResult(
                    candidate_id, kept=True, mask=candidate.mask, bbox=candidate.bbox
                )
            },
        ),
        (),
    )

    # When: the same candidate identity appears twice in the stage batch.
    payload = startup_trace_source_payload(
        (
            StartupCandidate(candidate, "image-001", ()),
            StartupCandidate(candidate, "image-001", ()),
        ),
        result,
        {},
    )

    # Then: the claim audit reports the collision instead of silently
    # asserting zero fabrication.
    audit = payload["no_fake_claim_audit"]
    assert isinstance(audit, dict)
    assert audit["claimed_candidate_count"] == 2
    assert audit["fabricated_candidate_count"] == 1
    assert audit["status"] == "fail"


def _kept_candidate_with_one_citation(
    tmp_path: Path,
) -> tuple[tuple[StartupCandidate, ...], AnomalyGroupingResult]:
    candidate_id = CandidateId("candidate-cited")
    candidate = _candidate(tmp_path, candidate_id, BoundingBox(0.0, 0.0, 20.0, 20.0))
    card: JsonObject = {"source_citation_ids": ["citation-001"]}
    result = AnomalyGroupingResult(
        RelationMergeResult(
            (),
            {
                candidate_id: CandidateRelationResult(
                    candidate_id, kept=True, mask=candidate.mask, bbox=candidate.bbox
                )
            },
        ),
        (),
    )
    return (StartupCandidate(candidate, "image-001", (card,)),), result


def test_trace_source_resolves_citation_with_matching_retrieval_detail(
    tmp_path: Path,
) -> None:
    # Given: a retrieval detail with a real page number for the cited chunk.
    candidates, result = _kept_candidate_with_one_citation(tmp_path)
    citation_details: dict[str, JsonObject] = {
        "citation-001": {
            "citation_id": "citation-001",
            "page_number": 172,
            "score": 0.86,
            "source_citation": "example.pdf",
        }
    }

    # When: startup trace-source payload resolves the candidate's citation.
    payload = startup_trace_source_payload(candidates, result, citation_details)

    # Then: the citation is exported with its real provenance instead of the
    # permanently-stubbed non-exportable placeholder.
    payload_candidates = payload["candidates"]
    assert _is_json_objects(payload_candidates)
    assert payload_candidates[0]["citations"] == [
        {
            "citation_id": "citation-001",
            "status": "exported",
            "source_citation": "example.pdf",
            "title": "example.pdf",
            "page_number": 172,
            "score": 0.86,
        }
    ]


def test_trace_source_keeps_citation_non_exportable_without_retrieval_match(
    tmp_path: Path,
) -> None:
    # Given: no retrieval detail resolves the candidate's cited chunk.
    candidates, result = _kept_candidate_with_one_citation(tmp_path)

    # When: startup trace-source payload is prepared with an empty lookup.
    payload = startup_trace_source_payload(candidates, result, {})

    # Then: the citation falls back to the honest non-exportable status.
    payload_candidates = payload["candidates"]
    assert _is_json_objects(payload_candidates)
    assert payload_candidates[0]["citations"] == [
        {"citation_id": "citation-001", "status": "non_exportable_corpus_citation"}
    ]


def test_trace_source_payload_carries_original_image_bbox(tmp_path: Path) -> None:
    # Given: a kept candidate whose bbox is already original-image-space
    # (restored upstream by mask_refining before anomaly_grouping ever sees it).
    candidates, result = _kept_candidate_with_one_citation(tmp_path)

    # When: startup trace-source payload is prepared for report generation.
    payload = startup_trace_source_payload(candidates, result, {})

    # Then: the candidate's bbox is exposed for downstream overlay rendering.
    payload_candidates = payload["candidates"]
    assert _is_json_objects(payload_candidates)
    assert payload_candidates[0]["bbox"] == {
        "x_min": 0.0,
        "y_min": 0.0,
        "x_max": 20.0,
        "y_max": 20.0,
    }


def _request(tmp_path: Path, *, dry_run: bool = False) -> _StageRequest:
    return _StageRequest(stage_paths(tmp_path, "project-001"), dry_run)


def _run_anomaly_grouping_stage(request: _StageRequest) -> int:
    return run_anomaly_grouping_stage(request)


def _write_upstream_inputs(
    tmp_path: Path,
    paths: StagePathMap,
    *,
    image_field: str = "image_id",
) -> None:
    parent_candidate_id = "rough-parent-001"
    refined_candidate_id = "refined-candidate-001"
    records_path = (
        paths.mask_refining / "refined" / refined_candidate_id / "records.json"
    )
    _write_json(
        records_path,
        [
            {
                "accepted": True,
                "bbox_xyxy": [0.0, 0.0, 20.0, 20.0],
                "image": "image-001",
                "prompt": "white surface deposit",
            }
        ],
    )
    mask = rect_mask(tmp_path, refined_candidate_id, BoundingBox(0.0, 0.0, 20.0, 20.0))
    _write_object_mask(paths, "image-001", "object-001")
    _write_jsonl(
        paths.mask_refining / "refined_records.jsonl",
        (
            {
                "accepted_candidates": [
                    {
                        "bbox_xyxy": [0.0, 0.0, 20.0, 20.0],
                        "original_bbox_xyxy": [0.0, 0.0, 20.0, 20.0],
                        "candidate_id": refined_candidate_id,
                        image_field: "image-001",
                        "prompt": "white surface deposit",
                        "source_object_id": "object-001",
                        "source_view_id": "view-001",
                        "qwen_final_success": True,
                        "qwen_report_display_text": "white deposit observed",
                        "qwen_confidence": 0.75,
                        "mask_path": mask.path,
                        "mask_sha256": mask.sha256,
                    }
                ],
                "accepted_candidate_ids": [refined_candidate_id],
                "detector_lane": "owlv2_sam2",
                "diagnostics": [],
                "model_lane": "owlv2",
                "prompt_texts": ["white surface deposit"],
                "rag_parent_candidate_id": parent_candidate_id,
                "records_json": str(records_path),
                "status": "executed",
            },
        ),
    )
    _write_jsonl(
        paths.rag / "rag_candidate_evidence.jsonl",
        (
            {
                "evidence_reason": None,
                "evidence_state": "rag_evidence_ready",
                "lane": "owlv2_sam2",
                "matched_chunk_ids": ["chunk-001"],
                "matched_citation_ids": ["citation-001"],
                "prompt_text": "white surface deposit",
                "query_id": "query-001",
                "rag_parent_candidate_id": parent_candidate_id,
                "rough_record_index": 0,
                "rough_record_path": "lane-a/object-001/owlv2_sam2/records.json",
                "top_chunk_id": "chunk-001",
                "top_citation_id": "citation-001",
                "top_result_rank": 1,
                "top_retrieval_score": 7.5,
            },
        ),
    )
    _write_jsonl(paths.rag / "prompt_rag_results.jsonl", ())
    _write_jsonl(
        paths.rag / "rag_visual_concept_cards.jsonl",
        (
            {
                "concept_card_id": "card-001",
                "concept_family": "deposit",
                "context_terms": ["surface"],
                "descriptor_terms": ["white", "powdery"],
                "material_terms": ["stone"],
                "provenance_strength": "strong",
                "rag_parent_candidate_id": parent_candidate_id,
                "raw_retrieved_sentence": "white powdery deposit on stone",
                "retrieval_score": 7.5,
                "source_citation_ids": ["citation-001"],
                "visual_cue": {
                    "boundary_relation": "interior",
                    "color_bucket": "white",
                    "confidence": 0.91,
                    "morphology": "crust",
                    "reasons": ["fixture"],
                    "size_class": "local",
                    "texture_proxy": "powdery",
                },
            },
        ),
    )


def _write_json(path: Path, payload: JsonValue) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


# run_anomaly_grouping_stage's off-object filter requires a preprocessing
# object silhouette mask for every candidate's (image_id, source_object_id).
# Covering the whole canvas keeps every rect_mask-based candidate fixture
# fully "on object" unless a test deliberately wants to exercise the filter.
def _write_object_mask(paths: StagePathMap, image_id: str, object_id: str) -> None:
    mask_dir = paths.preprocessing / "assets" / "objects" / image_id / object_id
    mask_dir.mkdir(parents=True, exist_ok=True)
    _ = rect_mask(mask_dir, "mask", BoundingBox(0.0, 0.0, 200.0, 200.0))


def _write_jsonl(path: Path, rows: tuple[Mapping[str, JsonValue], ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _read_json(path: Path) -> JsonObject:
    return parse_json_object(path.read_text(encoding="utf-8"))
