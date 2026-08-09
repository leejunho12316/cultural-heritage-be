from __future__ import annotations

from pathlib import Path


def test_vca_api_contract_documents_upload_completion_errors() -> None:
    # Given: the browser-facing VCA API contract.
    contract_path = Path(__file__).resolve().parents[1] / "docs" / "vca-api-contract.md"

    # When: the contract content is inspected.
    content = contract_path.read_text(encoding="utf-8")

    # Then: signed upload completion requirements are explicit for clients.
    assert "UPLOAD_NOT_VERIFIED" in content
    assert "SIGNED_PUT" in content
    assert "DIRECT_COMPLETE" in content
    assert "VCA_LOCAL_DIRECT_COMPLETE_ENABLED=true" in content
    assert "local/test-only" in content


def test_vca_api_contract_documents_jsonb_report_persistence() -> None:
    # Given: the browser-facing VCA API contract.
    contract_path = Path(__file__).resolve().parents[1] / "docs" / "vca-api-contract.md"

    # When: the contract content is inspected.
    content = contract_path.read_text(encoding="utf-8")

    # Then: the unchanged report endpoint is backed by JSONB report persistence.
    for expected_text in (
        "assessment_report.report_json",
        "GET /api/vca/{artifactId}/runs/{assessmentRunId}/report",
        "summary.overallCondition",
        "summary.riskLevel",
        "normalized report_*",
        "removed/deferred",
    ):
        assert expected_text in content
