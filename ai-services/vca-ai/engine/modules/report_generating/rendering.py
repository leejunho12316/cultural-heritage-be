"""Small HTML renderers for static evidence reports."""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from modules.report_generating.models import (
        CitationRecord,
        TraceCandidate,
        TraceSource,
    )

FINAL_REPORT_DISCLAIMER: Final = (
    "비진단 안내: 이 보고서는 AI 모델이 생성한 결과이며, "
    "문화유산에 대한 판단이 아닙니다. "
    "최종 판단을 위한 자료로만 활용해야 합니다."
)


def trace_index_html(source: TraceSource) -> str:
    """Render the trace landing page with all candidate navigation."""
    candidate_links = "".join(
        (
            f'<li><a href="candidates/{escape(candidate.candidate_id)}/index.html">'
            f"{escape(candidate.candidate_id)}</a> "
            f"({escape(candidate.followup_mode)})</li>"
        )
        for candidate in source.candidates
    )
    body = (
        "<h1>Trace Report</h1><p>Evidence Trace</p><p>Candidate Evidence</p>"
        f"<p>Budget Status: {escape(str(source.budget))}</p>"
        f"<dl>{_source_metadata_html(source)}</dl>"
        f"<ul>{candidate_links}</ul>"
    )
    return _document("Trace Report", body)


def candidate_html(candidate: TraceCandidate) -> str:
    """Render one candidate's provenance without diagnostic claims."""
    body = (
        f"<h1>Candidate {escape(candidate.candidate_id)}</h1>"
        '<p><a href="../../index.html">Trace Report</a></p>'
        f"{_candidate_provenance_html(candidate)}"
    )
    return _document(f"Candidate {candidate.candidate_id}", body)


def _candidate_provenance_html(candidate: TraceCandidate) -> str:
    citations = "".join(_citation_html(record) for record in candidate.citations)
    fields = (
        ("Concept family", candidate.concept_family),
        ("Display summary", candidate.hybrid_descriptor),
        ("Follow-up mode", candidate.followup_mode),
        ("Follow-up reason", candidate.followup_reason),
        ("Trigger priority", candidate.trigger_priority),
        (
            "Selected parent target",
            (
                f"{candidate.selected_parent_target_type}: "
                f"{candidate.selected_parent_target_id}"
            ),
        ),
        ("Terminal status", candidate.terminal_status),
        ("Relation authority outcome", candidate.relation_authority_outcome),
        ("Duplicate key", candidate.duplicate_suppression_key),
        ("rag_query", str(candidate.rag_query)),
        ("generated_prompts", str(candidate.generated_prompts)),
        ("reopen", str(candidate.reopen)),
        ("coverage_metrics", str(candidate.coverage_metrics)),
        ("skip_reason", str(candidate.skip_reason)),
    )
    return (
        f"<dl>{_details_html(fields)}</dl><h2>Citation export status</h2>"
        f"<ul>{citations}</ul>"
    )


def final_index_html(source: TraceSource) -> str:
    """Render a Korean evidence-only final report from verified trace metadata."""
    candidate_rows = "".join(
        (
            "<li>"
            f"{escape(candidate.candidate_id)}: {escape(candidate.concept_family)} "
            f"({escape(candidate.terminal_status)})"
            f"{_candidate_provenance_html(candidate)}"
            "</li>"
        )
        for candidate in source.candidates
    )
    outcome = "완료" if source.run_summary.final_success else "불완전"
    body = (
        "<h1>최종 한국어 보고서</h1>"
        f"<p>{escape(FINAL_REPORT_DISCLAIMER)}</p><p>Evidence Trace</p>"
        '<p><a href="../report/index.html">Trace Report</a></p>'
        f"<p>실행 상태: {escape(source.run_summary.status)} ({outcome})</p>"
        f"<dl>{_source_metadata_html(source)}</dl>"
        f"<ul>{candidate_rows}</ul>"
    )
    return _document("최종 한국어 보고서", body)


def _citation_html(record: CitationRecord) -> str:
    field_values = (
        ("status", record.status),
        ("citation_id", record.citation_id),
        ("chunk_id", record.chunk_id),
        ("source_citation", record.source_citation),
        ("source_type", record.source_type),
        ("license_status", record.license_status),
        ("title", record.title),
        ("score", record.score),
        ("page_number", record.page_number),
    )
    fields = tuple(
        (label, str(value)) for label, value in field_values if value is not None
    )
    return (
        '<li data-report-role="citation-source"><dl>'
        f"{_details_html(fields)}</dl></li>"
    )


def _source_metadata_html(source: TraceSource) -> str:
    return _details_html(
        (
            ("scale_metadata", str(source.scale_metadata)),
            ("tile_metadata", str(source.tile_metadata)),
        )
    )


def _details_html(fields: tuple[tuple[str, str], ...]) -> str:
    return "".join(
        f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>" for label, value in fields
    )


def _document(title: str, body: str) -> str:
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:auto;max-width:72rem;"
        "padding:1rem;overflow-wrap:anywhere}dt{font-weight:700;margin-top:1rem}"
        "dd{margin-left:0}a{color:#164e63}</style></head><body>"
        f"{body}</body></html>"
    )
