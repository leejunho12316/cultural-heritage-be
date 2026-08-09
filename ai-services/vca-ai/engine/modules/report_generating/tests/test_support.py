from __future__ import annotations

from typing import TYPE_CHECKING

from modules.report_generating.io import write_json
from modules.report_generating.runner import main as runner_main

if TYPE_CHECKING:
    from pathlib import Path

    from modules.report_generating.models import JsonObject


def generate_reports(tmp_path: Path, *, final_success: bool) -> Path:
    source_path = write_source(tmp_path, final_success=final_success)
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    write_json(
        request_path,
        {
            "schema": "report_generating_request_v1",
            "workspace_root": str(tmp_path),
            "run_root": str(tmp_path / "run"),
            "trace_source_path": str(source_path),
        },
    )
    assert (
        runner_main(
            ("--input-json", str(request_path), "--output-json", str(result_path))
        )
        == 0
    )
    return tmp_path / "run"


def write_source(tmp_path: Path, *, final_success: bool = True) -> Path:
    path = tmp_path / "trace-source.json"
    write_json(path, trace_payload(final_success=final_success))
    return path


def trace_payload(*, final_success: bool = True) -> JsonObject:
    return {
        "schema": "report_trace_source_v1",
        "run_summary": {"status": "complete", "final_success": final_success},
        "no_fake_claim_audit": {
            "status": "pass",
            "claimed_candidate_count": 2,
            "fabricated_candidate_count": 0,
            "runner_invoked": True,
        },
        "images": [{"image_id": "image-a", "summary": "source evidence"}],
        "candidates": [
            {
                "candidate_id": "candidate-a",
                "image_id": "image-a",
                "final_success": final_success,
                "concept_family": "surface-mark",
                "hybrid_descriptor": "observed trace descriptor",
                "followup_mode": "automatic",
                "followup_reason": "coverage gap",
                "trigger_priority": "high",
                "selected_parent_target_type": "candidate_id",
                "selected_parent_target_id": "candidate-a",
                "terminal_status": "failed_no_citation",
                "relation_authority_outcome": "same_anomaly_duplicate",
                "duplicate_suppression_key": "duplicate-a",
                "citations": [
                    {
                        "status": "non_exportable_corpus_citation",
                        "citation_id": "citation-hidden",
                    }
                ],
            },
            {
                "candidate_id": "candidate-b",
                "image_id": "image-a",
                "final_success": final_success,
                "concept_family": "edge-feature",
                "hybrid_descriptor": "second trace descriptor",
                "followup_mode": "user_requested",
                "followup_reason": "review request",
                "trigger_priority": "medium",
                "selected_parent_target_type": "same_anomaly_group_id",
                "selected_parent_target_id": "group-b",
                "terminal_status": "completed",
                "relation_authority_outcome": "co_located_distinct_anomaly",
                "duplicate_suppression_key": "duplicate-b",
                "citations": [
                    {
                        "status": "exported",
                        "citation_id": "citation-a",
                        "title": "Export source",
                        "page_number": 3,
                    }
                ],
            },
        ],
        "relations": ["structured relation output"],
        "budget": {"status": "within_budget"},
    }
