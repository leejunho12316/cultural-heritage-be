from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from modules.report_generating.browser_qa import main as browser_qa_main
from modules.report_generating.final import generate_final_report
from modules.report_generating.io import (
    parse_trace_source,
    read_json_object,
    write_json,
)
from modules.report_generating.runner import main as runner_main
from modules.report_generating.tests.test_support import (
    generate_reports,
    trace_payload,
    write_source,
)
from modules.report_generating.verification import (
    verify_final_report,
    verify_trace_report,
)
from modules.shared import ContractValidationError

if TYPE_CHECKING:
    from pathlib import Path

def test_runner_writes_verified_trace_and_final_reports(tmp_path: Path) -> None:
    # Given: a typed JSON request and export-safe trace source.
    source_path = write_source(tmp_path)
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

    # When: the public standalone runner processes the request.
    exit_code = runner_main(
        ("--input-json", str(request_path), "--output-json", str(result_path))
    )

    # Then: both verified report surfaces and their result artifact exist.
    assert exit_code == 0
    result = read_json_object(result_path)
    assert result["verification_status"] == "pass"
    run_root = tmp_path / "run"
    assert (run_root / "report" / "verification" / "receipt.json").is_file()
    assert (run_root / "final_report" / "verification" / "receipt.json").is_file()
    final_html = (run_root / "final_report" / "index.html").read_text(encoding="utf-8")
    assert "최종 한국어 보고서" in final_html
    assert "비진단" in final_html
    assert "모델이 생성한 결과이며, 문화유산에 대한 판단이 아닙니다" in final_html
    assert "최종 판단을 위한 자료로만 활용해야 합니다" in final_html
    assert "Evidence Trace" in final_html


def test_runner_reports_missing_input_json_to_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a request path that was never written.
    request_path = tmp_path / "missing-request.json"
    result_path = tmp_path / "result.json"

    # When: the runner is invoked against the missing input file.
    exit_code = runner_main(
        ("--input-json", str(request_path), "--output-json", str(result_path))
    )

    # Then: it fails closed, and the real reason reaches stderr instead of
    # being swallowed silent (no output.json is written either).
    assert exit_code != 0
    assert not result_path.exists()
    err = capsys.readouterr().err
    assert "report_generating: failed:" in err
    assert "FileNotFoundError" in err


def test_runner_rejects_trace_source_with_fabricated_candidate_claim(
    tmp_path: Path,
) -> None:
    # Given: a trace source whose claim audit reports a fabricated candidate
    # (e.g. a duplicated candidate identity caught upstream in anomaly_grouping).
    payload = trace_payload()
    audit = payload["no_fake_claim_audit"]
    assert isinstance(audit, dict)
    audit["fabricated_candidate_count"] = 1
    audit["status"] = "fail"
    source_path = tmp_path / "trace-source.json"
    write_json(source_path, payload)
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

    # When: the runner tries to build a report from that trace source.
    exit_code = runner_main(
        ("--input-json", str(request_path), "--output-json", str(result_path))
    )

    # Then: the report build fails closed instead of publishing a report
    # whose candidate count cannot be trusted.
    assert exit_code != 0


def test_final_refuses_digest_mismatch_when_incomplete(tmp_path: Path) -> None:
    # Given: a generated incomplete trace report with a passing receipt.
    run_root = generate_reports(tmp_path, final_success=False)
    trace_index = run_root / "report" / "index.html"
    _ = trace_index.write_text(
        trace_index.read_text(encoding="utf-8") + "<p>tampered</p>", encoding="utf-8"
    )

    # When: final generation tries to use the stale trace verification receipt.
    with pytest.raises(ContractValidationError, match="digest mismatch"):
        _ = generate_final_report(run_root)

    # Then: the incomplete flag did not weaken trace integrity enforcement.


def test_trace_verifier_rejects_only_index_report(tmp_path: Path) -> None:
    # Given: an incomplete trace artifact with only the index page.
    report_root = tmp_path / "run" / "report"
    report_root.mkdir(parents=True)
    _ = (report_root / "index.html").write_text("Trace Report", encoding="utf-8")

    # When: static trace verification runs.
    receipt = verify_trace_report(tmp_path / "run")

    # Then: it records a machine-readable failure rather than accepting the index alone.
    assert receipt.passed is False
    assert receipt.reason == "trace report requires index and metadata"


def test_parser_rejects_internal_corpus_citation() -> None:
    # Given: a trace source that attempts to pass an internal corpus citation.
    payload = trace_payload()
    candidates = payload["candidates"]
    assert isinstance(candidates, list)
    candidate = candidates[0]
    assert isinstance(candidate, dict)
    citations = candidate["citations"]
    assert isinstance(citations, list)
    citation = citations[0]
    assert isinstance(citation, dict)
    citation["record_type"] = "CorpusCitation"

    # When: the source crosses the typed report boundary.
    with pytest.raises(ContractValidationError, match="CorpusCitation"):
        _ = parse_trace_source(payload)

    # Then: no internal citation record can reach report rendering.


def test_trace_renders_adapter_status_and_followup_modes(tmp_path: Path) -> None:
    # Given: both automatic and user-requested terminal follow-up records.
    run_root = generate_reports(tmp_path, final_success=True)

    # When: candidate pages are rendered from the trace source.
    automatic = (
        run_root / "report" / "candidates" / "candidate-a" / "index.html"
    ).read_text(encoding="utf-8")
    requested = (
        run_root / "report" / "candidates" / "candidate-b" / "index.html"
    ).read_text(encoding="utf-8")

    # Then: explicit adapter status and both filterable modes remain visible.
    assert "non_exportable_corpus_citation" in automatic
    assert "automatic" in automatic
    assert "user_requested" in requested
    assert "Selected parent target" in requested
    assert "Terminal status" in requested


def test_final_verifier_allows_diagnostic_language(tmp_path: Path) -> None:
    # Given: a valid final report modified with model-generated terms.
    run_root = generate_reports(tmp_path, final_success=True)
    final_index = run_root / "final_report" / "index.html"
    _ = final_index.write_text(
        final_index.read_text(encoding="utf-8") + "<p>진단 치료 중증 보존</p>",
        encoding="utf-8",
    )

    # When: the final static verifier reviews the surface.
    receipt = verify_final_report(run_root)

    # Then: final wording is allowed because the report carries a usage disclaimer.
    assert receipt.passed is True
    assert receipt.reason is None


def test_final_verifier_allows_preservation_terms_inside_citations(
    tmp_path: Path,
) -> None:
    # Given: export-safe trace evidence whose bibliography contains preservation terms.
    payload = trace_payload()
    candidates = payload["candidates"]
    assert isinstance(candidates, list)
    candidate = candidates[1]
    assert isinstance(candidate, dict)
    citations = candidate["citations"]
    assert isinstance(citations, list)
    citation = citations[0]
    assert isinstance(citation, dict)
    citation.update(
        {
            "citation_id": "문화유산 보존 국제 심포지엄:chunk-0001:citation",
            "source_citation": "문화유산 보존 국제 심포지엄",
            "title": "문화유산 보존 국제 심포지엄",
        }
    )
    source_path = tmp_path / "trace-source.json"
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    write_json(source_path, payload)
    write_json(
        request_path,
        {
            "schema": "report_generating_request_v1",
            "workspace_root": str(tmp_path),
            "run_root": str(tmp_path / "run"),
            "trace_source_path": str(source_path),
        },
    )

    # When: the public runner generates trace and final reports.
    exit_code = runner_main(
        ("--input-json", str(request_path), "--output-json", str(result_path))
    )

    # Then: citation source text does not count as generated diagnostic language.
    assert exit_code == 0
    result = read_json_object(result_path)
    assert result["verification_status"] == "pass"


def test_final_verifier_allows_preservation_claim_outside_citations(
    tmp_path: Path,
) -> None:
    # Given: a valid final report modified with generated preservation wording.
    run_root = generate_reports(tmp_path, final_success=True)
    final_index = run_root / "final_report" / "index.html"
    _ = final_index.write_text(
        final_index.read_text(encoding="utf-8") + "<p>보존 처리 필요</p>",
        encoding="utf-8",
    )

    # When: the final static verifier reviews the surface.
    receipt = verify_final_report(run_root)

    # Then: the verifier accepts final report wording under the disclaimer policy.
    assert receipt.passed is True
    assert receipt.reason is None


def test_browser_qa_writes_static_surface_receipt(tmp_path: Path) -> None:
    # Given: fixture HTML generated through the real report runner.
    run_root = generate_reports(tmp_path, final_success=True)
    out_dir = tmp_path / "browser-qa"

    # When: the public browser QA command checks both requested viewports.
    exit_code = browser_qa_main(
        (
            "--run-root",
            str(run_root),
            "--viewports",
            "1440x1000",
            "390x844",
            "--out-dir",
            str(out_dir),
        )
    )

    # Then: deterministic static checks produce a passing machine-readable receipt.
    assert exit_code == 0
    receipt = read_json_object(out_dir / "receipt.json")
    assert receipt["status"] == "pass"
    assert receipt["horizontal_overflow"] == "not_rendered"
