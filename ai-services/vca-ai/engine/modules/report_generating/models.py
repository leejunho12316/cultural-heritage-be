"""Typed contracts for evidence-only report generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from modules.shared import (
    FINAL_REPORT_SCHEMA_VERSION,
    FINAL_REPORT_STATIC_VERIFICATION_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    REPORT_STATIC_VERIFICATION_SCHEMA_VERSION,
)

if TYPE_CHECKING:
    from pathlib import Path

REPORT_GENERATING_REQUEST_SCHEMA: Final = "report_generating_request_v1"
TRACE_SOURCE_SCHEMA: Final = "report_trace_source_v1"
TRACE_METADATA_SCHEMA: Final = REPORT_SCHEMA_VERSION
TRACE_VERIFICATION_SCHEMA: Final = REPORT_STATIC_VERIFICATION_SCHEMA_VERSION
FINAL_METADATA_SCHEMA: Final = FINAL_REPORT_SCHEMA_VERSION
FINAL_VERIFICATION_SCHEMA: Final = FINAL_REPORT_STATIC_VERIFICATION_SCHEMA_VERSION

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ReportGeneratingRequest:
    """Filesystem inputs for one standalone report generation request."""

    workspace_root: Path
    run_root: Path
    trace_source_path: Path


@dataclass(frozen=True, slots=True)
class RunSummary:
    """Final pipeline outcome carried into the report surface."""

    status: str
    final_success: bool


@dataclass(frozen=True, slots=True)
class NoFakeClaimAudit:
    """Audit facts proving the trace does not invent candidates."""

    status: str
    claimed_candidate_count: int
    fabricated_candidate_count: int
    runner_invoked: bool

    @property
    def passed(self) -> bool:
        """Return whether this audit supports report publication."""
        return (
            self.status == "pass"
            and self.fabricated_candidate_count == 0
            and self.runner_invoked
        )


@dataclass(frozen=True, slots=True)
class CitationRecord:
    """A report-safe export citation or explicit non-exportable status."""

    status: str
    citation_id: str
    chunk_id: str | None
    source_citation: str | None
    source_type: str | None
    license_status: str | None
    title: str | None
    score: float | None
    page_number: int | None

    @property
    def exportable(self) -> bool:
        """Return whether the citation is valid report provenance."""
        return self.status == "exported"


@dataclass(frozen=True, slots=True)
class TraceCandidate:
    """Candidate evidence rendered into the trace and final report."""

    candidate_id: str
    image_id: str
    final_success: bool
    concept_family: str
    hybrid_descriptor: str
    followup_mode: str
    followup_reason: str
    trigger_priority: str
    selected_parent_target_type: str
    selected_parent_target_id: str
    terminal_status: str
    relation_authority_outcome: str
    duplicate_suppression_key: str
    citations: tuple[CitationRecord, ...]
    rag_query: JsonObject = field(default_factory=dict)
    generated_prompts: tuple[JsonObject, ...] = ()
    reopen: JsonObject = field(default_factory=dict)
    coverage_metrics: tuple[JsonObject, ...] = ()
    skip_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ImageSummary:
    """Source image facts without image reconstruction."""

    image_id: str
    summary: str


@dataclass(frozen=True, slots=True)
class TraceSource:
    """All report-safe evidence consumed by trace generation."""

    run_summary: RunSummary
    no_fake_claim_audit: NoFakeClaimAudit
    images: tuple[ImageSummary, ...]
    candidates: tuple[TraceCandidate, ...]
    relations: tuple[str, ...]
    budget: JsonObject
    scale_metadata: JsonObject = field(default_factory=dict)
    tile_metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VerificationReceipt:
    """Machine-readable verifier output."""

    schema: str
    verification_status: str
    run_root: str
    artifact_root: str
    reason: str | None
    digests: JsonObject

    @property
    def passed(self) -> bool:
        """Return whether this receipt authorizes the next stage."""
        return self.verification_status == "pass"
