"""Trace report generation from typed report-safe evidence."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from modules.report_generating.io import write_json
from modules.report_generating.models import (
    TRACE_METADATA_SCHEMA,
    JsonObject,
    TraceCandidate,
    TraceSource,
)
from modules.report_generating.rendering import candidate_html, trace_index_html
from modules.shared import ensure_no_symlink_leaf

if TYPE_CHECKING:
    from pathlib import Path


# runner.py의 run_report_generation이 첫 단계로 호출한다. trace 인덱스 HTML,
# 후보별 개별 페이지, metadata.json을 run_root 아래에 기록한다.
def generate_trace_report(run_root: Path, source: TraceSource) -> Path:
    """Write trace HTML, metadata, and candidate pages below a run root."""
    report_root = ensure_no_symlink_leaf(
        run_root / "report", "trace report directory is a symlink"
    )
    candidates_root = ensure_no_symlink_leaf(
        report_root / "candidates", "trace candidate directory is a symlink"
    )
    report_root.mkdir(parents=True, exist_ok=True)
    candidates_root.mkdir(exist_ok=True)
    index_path = ensure_no_symlink_leaf(
        report_root / "index.html", "trace index is a symlink"
    )
    _write_text(index_path, trace_index_html(source))
    candidate_digests: JsonObject = {}
    for candidate in source.candidates:
        candidate_root = ensure_no_symlink_leaf(
            candidates_root / candidate.candidate_id,
            "trace candidate directory is a symlink",
        )
        candidate_root.mkdir(exist_ok=True)
        candidate_path = candidate_root / "index.html"
        safe_path = ensure_no_symlink_leaf(
            candidate_path, "trace candidate page is a symlink"
        )
        _write_text(safe_path, candidate_html(candidate))
        candidate_digests[candidate.candidate_id] = _sha256(safe_path)
    metadata_path = ensure_no_symlink_leaf(
        report_root / "metadata.json", "trace metadata is a symlink"
    )
    write_json(
        metadata_path,
        _metadata(run_root, source, index_path, candidate_digests),
    )
    return report_root


# generate_trace_report에서 호출된다. metadata.json에 기록할 전체 페이로드를
# 만든다(이후 final.py와 verification 모듈들이 이 구조를 그대로 재파싱한다).
def _metadata(
    run_root: Path,
    source: TraceSource,
    index_path: Path,
    candidate_digests: JsonObject,
) -> JsonObject:
    return {
        "schema": TRACE_METADATA_SCHEMA,
        "run_root": str(run_root.resolve()),
        "run_summary": {
            "status": source.run_summary.status,
            "final_success": source.run_summary.final_success,
        },
        "no_fake_claim_audit": {
            "status": source.no_fake_claim_audit.status,
            "claimed_candidate_count": (
                source.no_fake_claim_audit.claimed_candidate_count
            ),
            "fabricated_candidate_count": (
                source.no_fake_claim_audit.fabricated_candidate_count
            ),
            "runner_invoked": source.no_fake_claim_audit.runner_invoked,
        },
        "images": [
            {"image_id": image.image_id, "summary": image.summary}
            for image in source.images
        ],
        "candidates": [
            _candidate_metadata(candidate) for candidate in source.candidates
        ],
        "relations": list(source.relations),
        "budget": source.budget,
        "scale_metadata": source.scale_metadata,
        "tile_metadata": source.tile_metadata,
        "digests": {
            "index_html_sha256": _sha256(index_path),
            "candidate_pages_sha256": candidate_digests,
        },
    }


# _metadata에서 후보마다 호출된다. TraceCandidate를 metadata.json의 후보
# 항목 하나로 직렬화한다.
def _candidate_metadata(candidate: TraceCandidate) -> JsonObject:
    return {
        "candidate_id": candidate.candidate_id,
        "image_id": candidate.image_id,
        "final_success": candidate.final_success,
        "concept_family": candidate.concept_family,
        "hybrid_descriptor": candidate.hybrid_descriptor,
        "followup_mode": candidate.followup_mode,
        "followup_reason": candidate.followup_reason,
        "trigger_priority": candidate.trigger_priority,
        "selected_parent_target_type": candidate.selected_parent_target_type,
        "selected_parent_target_id": candidate.selected_parent_target_id,
        "terminal_status": candidate.terminal_status,
        "relation_authority_outcome": candidate.relation_authority_outcome,
        "duplicate_suppression_key": candidate.duplicate_suppression_key,
        "citations": [
            {
                "status": citation.status,
                "citation_id": citation.citation_id,
                "chunk_id": citation.chunk_id,
                "source_citation": citation.source_citation,
                "source_type": citation.source_type,
                "license_status": citation.license_status,
                "title": citation.title,
                "score": citation.score,
                "page_number": citation.page_number,
            }
            for citation in candidate.citations
        ],
        "rag_query": candidate.rag_query,
        "generated_prompts": list(candidate.generated_prompts),
        "reopen": candidate.reopen,
        "coverage_metrics": list(candidate.coverage_metrics),
        "skip_reason": candidate.skip_reason,
        "bbox": None
        if candidate.bbox is None
        else {
            "x_min": candidate.bbox.x_min,
            "y_min": candidate.bbox.y_min,
            "x_max": candidate.bbox.x_max,
            "y_max": candidate.bbox.y_max,
        },
        "polygon": None
        if candidate.polygon is None
        else [[point[0], point[1]] for point in candidate.polygon],
        "qwen_final_success": candidate.qwen_final_success,
        "qwen_report_display_text": candidate.qwen_report_display_text,
        "qwen_confidence": candidate.qwen_confidence,
    }


def _write_text(path: Path, content: str) -> None:
    _ = path.write_text(content, encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
