"""Stable public APIs for verified evidence report generation."""

from modules.report_generating.final import generate_final_report
from modules.report_generating.io import (
    parse_trace_source,
    read_request,
    read_trace_source,
)
from modules.report_generating.models import (
    CitationRecord,
    NoFakeClaimAudit,
    ReportGeneratingRequest,
    RunSummary,
    TraceCandidate,
    TraceCandidateBbox,
    TraceSource,
    VerificationReceipt,
)
from modules.report_generating.trace import generate_trace_report
from modules.report_generating.verification import (
    trace_receipt_integrity_error,
    verify_final_report,
    verify_trace_report,
)

__all__ = (
    "CitationRecord",
    "NoFakeClaimAudit",
    "ReportGeneratingRequest",
    "RunSummary",
    "TraceCandidate",
    "TraceCandidateBbox",
    "TraceSource",
    "VerificationReceipt",
    "generate_final_report",
    "generate_trace_report",
    "parse_trace_source",
    "read_request",
    "read_trace_source",
    "trace_receipt_integrity_error",
    "verify_final_report",
    "verify_trace_report",
)
