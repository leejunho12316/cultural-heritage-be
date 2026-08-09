from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from modules.report_generating.final import generate_final_report
from modules.report_generating.io import (
    parse_trace_source,
    read_json_object,
    write_json,
)
from modules.report_generating.tests.test_support import trace_payload
from modules.report_generating.trace import generate_trace_report
from modules.report_generating.verification import (
    verify_final_report,
    verify_trace_report,
)
from modules.shared import PathSafetyError

if TYPE_CHECKING:
    from pathlib import Path


def test_t9_metadata_survives_trace_final_and_html_surfaces(tmp_path: Path) -> None:
    # Given: trace evidence carries report-safe T9 metadata and an ExportCitation.
    payload = trace_payload()
    payload["scale_metadata"] = {"scale_unit_px": 12.5}
    payload["tile_metadata"] = {"tile_count": 4}
    candidates = payload["candidates"]
    assert isinstance(candidates, list)
    candidate = candidates[1]
    assert isinstance(candidate, dict)
    candidate.update(
        {
            "rag_query": {"terms": ["edge"], "descriptors": ["narrow"]},
            "generated_prompts": [
                {"generated_prompt_id": "prompt-a", "generated_prompt": "edge"}
            ],
            "reopen": {"status": "rag_reopen_completed"},
            "coverage_metrics": [{"name": "document_coverage", "value": 1.0}],
            "skip_reason": "not skipped",
            "bbox": {"x_min": 10.0, "y_min": 20.0, "x_max": 30.0, "y_max": 40.0},
            "polygon": [[10.0, 20.0], [30.0, 20.0], [30.0, 40.0], [10.0, 40.0]],
        }
    )
    citations = candidate["citations"]
    assert isinstance(citations, list)
    citation = citations[0]
    assert isinstance(citation, dict)
    citation.update(
        {
            "chunk_id": "chunk-a",
            "source_citation": "source-a",
            "source_type": "manual",
            "license_status": "licensed",
            "score": 0.91,
        }
    )
    source = parse_trace_source(payload)
    run_root = tmp_path / "run"

    # When: both report layers are generated from the verified trace source.
    report_root = generate_trace_report(run_root, source)
    assert verify_trace_report(run_root).passed
    final_root = generate_final_report(run_root)
    assert verify_final_report(run_root).passed

    # Then: native fields survive JSON metadata and remain visible on both surfaces.
    metadata = read_json_object(report_root / "metadata.json")
    assert metadata["scale_metadata"] == {"scale_unit_px": 12.5}
    assert metadata["tile_metadata"] == {"tile_count": 4}
    metadata_candidates = metadata["candidates"]
    assert isinstance(metadata_candidates, list)
    metadata_candidate = metadata_candidates[1]
    assert isinstance(metadata_candidate, dict)
    assert metadata_candidate["rag_query"] == candidate["rag_query"]
    assert metadata_candidate["generated_prompts"] == candidate["generated_prompts"]
    assert metadata_candidate["reopen"] == candidate["reopen"]
    assert metadata_candidate["coverage_metrics"] == candidate["coverage_metrics"]
    assert metadata_candidate["skip_reason"] == candidate["skip_reason"]
    assert metadata_candidate["citations"] == candidate["citations"]
    assert metadata_candidate["bbox"] == candidate["bbox"]
    assert metadata_candidate["polygon"] == candidate["polygon"]
    trace_html = (
        report_root / "candidates" / "candidate-b" / "index.html"
    ).read_text(encoding="utf-8")
    final_html = (final_root / "index.html").read_text(encoding="utf-8")
    for field in (
        "scale_metadata",
        "tile_metadata",
        "rag_query",
        "generated_prompts",
        "reopen",
        "coverage_metrics",
        "skip_reason",
        "chunk_id",
        "source_citation",
        "source_type",
        "license_status",
        "score",
    ):
        assert field in trace_html or field in final_html
        assert field in final_html


@pytest.mark.parametrize(
    "relative_directory",
    ["report", "report/candidates", "report/candidates/candidate-a"],
)
def test_trace_generation_rejects_symlinked_report_directories(
    tmp_path: Path, relative_directory: str
) -> None:
    # Given: an intermediate trace directory leaf points outside the run root.
    run_root = tmp_path / "run"
    external_directory = tmp_path / "external"
    external_directory.mkdir()
    symlink_path = run_root / relative_directory
    symlink_path.parent.mkdir(parents=True, exist_ok=True)
    symlink_path.symlink_to(external_directory, target_is_directory=True)
    source = parse_trace_source(trace_payload())

    # When: trace generation prepares its report directories.
    with pytest.raises(PathSafetyError):
        _ = generate_trace_report(run_root, source)

    # Then: no artifact is written through the directory symlink.
    assert tuple(external_directory.iterdir()) == ()


def test_final_generation_rejects_symlinked_final_report_directory(
    tmp_path: Path,
) -> None:
    # Given: a verified trace report and a final-report root linked outside the run.
    run_root = tmp_path / "run"
    source = parse_trace_source(trace_payload())
    _ = generate_trace_report(run_root, source)
    assert verify_trace_report(run_root).passed
    external_directory = tmp_path / "external-final"
    external_directory.mkdir()
    (run_root / "final_report").symlink_to(external_directory, target_is_directory=True)

    # When: final report generation starts.
    with pytest.raises(PathSafetyError):
        _ = generate_final_report(run_root)

    # Then: the external directory remains untouched.
    assert tuple(external_directory.iterdir()) == ()


@pytest.mark.parametrize(
    "relative_directory",
    ["report", "report/verification"],
)
def test_trace_verification_rejects_symlinked_receipt_directories(
    tmp_path: Path, relative_directory: str
) -> None:
    # Given: trace artifacts exist and the receipt path crosses a directory symlink.
    run_root = tmp_path / "run"
    external_directory = tmp_path / "external-trace-verification"
    external_directory.mkdir()
    if relative_directory != "report":
        source = parse_trace_source(trace_payload())
        _ = generate_trace_report(run_root, source)
    else:
        run_root.mkdir()
    symlink_path = run_root / relative_directory
    if symlink_path.exists():
        symlink_path.rmdir()
    symlink_path.parent.mkdir(parents=True, exist_ok=True)
    symlink_path.symlink_to(external_directory, target_is_directory=True)

    # When: trace verification tries to persist its receipt.
    with pytest.raises(PathSafetyError):
        _ = verify_trace_report(run_root)

    # Then: no receipt is written through the symlinked directory.
    assert tuple(external_directory.iterdir()) == ()


def test_final_verification_rejects_symlinked_receipt_directory(
    tmp_path: Path,
) -> None:
    # Given: final artifacts exist and the receipt directory points outside the run.
    run_root = tmp_path / "run"
    source = parse_trace_source(trace_payload())
    _ = generate_trace_report(run_root, source)
    assert verify_trace_report(run_root).passed
    _ = generate_final_report(run_root)
    external_directory = tmp_path / "external-final-verification"
    external_directory.mkdir()
    (run_root / "final_report" / "verification").symlink_to(
        external_directory, target_is_directory=True
    )

    # When: final verification tries to persist its receipt.
    with pytest.raises(PathSafetyError):
        _ = verify_final_report(run_root)

    # Then: no receipt is written through the symlinked directory.
    assert tuple(external_directory.iterdir()) == ()


@pytest.mark.parametrize("relative_leaf", ["index.html", "metadata.json"])
def test_trace_verification_rejects_symlinked_trace_leaves(
    tmp_path: Path, relative_leaf: str
) -> None:
    # Given: a trace artifact leaf points at identical bytes outside the report tree.
    run_root = tmp_path / "run"
    source = parse_trace_source(trace_payload())
    report_root = generate_trace_report(run_root, source)
    original_leaf = report_root / relative_leaf
    external_leaf = tmp_path / f"external-{relative_leaf}"
    _ = external_leaf.write_bytes(original_leaf.read_bytes())
    original_leaf.unlink()
    original_leaf.symlink_to(external_leaf)

    # When: trace verification reviews the pre-existing artifacts.
    receipt = verify_trace_report(run_root)

    # Then: it rejects the symlink rather than blessing external bytes.
    assert receipt.passed is False
    assert receipt.reason == "trace report leaf is a symlink"


def test_trace_verification_rejects_symlinked_candidate_page(
    tmp_path: Path,
) -> None:
    # Given: a candidate page leaf points at identical bytes outside the report tree.
    run_root = tmp_path / "run"
    source = parse_trace_source(trace_payload())
    report_root = generate_trace_report(run_root, source)
    candidate_leaf = report_root / "candidates" / "candidate-a" / "index.html"
    external_leaf = tmp_path / "external-candidate.html"
    _ = external_leaf.write_bytes(candidate_leaf.read_bytes())
    candidate_leaf.unlink()
    candidate_leaf.symlink_to(external_leaf)

    # When: trace verification reviews the candidate artifact.
    receipt = verify_trace_report(run_root)

    # Then: it rejects the symlinked candidate page.
    assert receipt.passed is False
    assert receipt.reason == "trace candidate page is a symlink"


def test_trace_verification_rejects_candidate_page_escape(
    tmp_path: Path,
) -> None:
    # Given: tampered metadata points a candidate page outside the report root.
    run_root = tmp_path / "run"
    source = parse_trace_source(trace_payload())
    report_root = generate_trace_report(run_root, source)
    escaped_candidate_id = "../../outside-candidate"
    escaped_page = run_root / "outside-candidate" / "index.html"
    escaped_page.parent.mkdir()
    _ = escaped_page.write_text("<h1>external candidate</h1>", encoding="utf-8")
    index_path = report_root / "index.html"
    _ = index_path.write_text(
        index_path.read_text(encoding="utf-8")
        + '<a href="candidates/../../outside-candidate/index.html">escape</a>',
        encoding="utf-8",
    )
    metadata_path = report_root / "metadata.json"
    metadata = read_json_object(metadata_path)
    candidates = metadata["candidates"]
    assert isinstance(candidates, list)
    first_candidate = candidates[0]
    assert isinstance(first_candidate, dict)
    first_candidate["candidate_id"] = escaped_candidate_id
    digests = metadata["digests"]
    assert isinstance(digests, dict)
    page_digests = digests["candidate_pages_sha256"]
    assert isinstance(page_digests, dict)
    del page_digests["candidate-a"]
    page_digests[escaped_candidate_id] = _sha256(escaped_page)
    digests["index_html_sha256"] = _sha256(index_path)
    write_json(metadata_path, metadata)

    # When: trace verification follows the tampered candidate path.
    receipt = verify_trace_report(run_root)

    # Then: it refuses to hash a candidate page outside the report root.
    assert receipt.passed is False
    assert receipt.reason == "trace candidate page escapes report root"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
