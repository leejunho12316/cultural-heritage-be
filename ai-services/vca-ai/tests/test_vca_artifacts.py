import json
from pathlib import Path

from app.services.vca_artifacts import load_vca_report


def _write_final_report_gate(engine_root: Path, project_name: str) -> None:
    final_report_root = (
        engine_root / "output" / "report_generating" / project_name / "final_report"
    )
    final_report_root.joinpath("verification").mkdir(parents=True)
    final_report_root.joinpath("metadata.json").write_text(
        json.dumps({"schema": "raw-image-final-report-v1", "final_success": True}),
        encoding="utf-8",
    )
    final_report_root.joinpath("verification", "receipt.json").write_text(
        json.dumps(
            {
                "schema": "raw-image-final-report-static-v1",
                "verification_status": "pass",
            }
        ),
        encoding="utf-8",
    )


def _write_startup_receipt(engine_root: Path, project_name: str) -> None:
    startup_path = (
        engine_root / "output" / "result" / project_name / "receipts" / "startup.json"
    )
    startup_path.parent.mkdir(parents=True)
    startup_path.write_text(
        json.dumps({"project_name": project_name, "status": "completed", "stages": []}),
        encoding="utf-8",
    )


def _write_report_metadata(engine_root: Path, project_name: str, candidates: list) -> None:
    metadata_path = (
        engine_root / "output" / "report_generating" / project_name / "report" / "metadata.json"
    )
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text(
        json.dumps({"candidates": candidates}), encoding="utf-8"
    )


def test_load_vca_report_builds_finding_with_real_candidate_and_citation(
    tmp_path: Path,
) -> None:
    # Given: a completed run with one kept candidate carrying an exported citation.
    engine_root = tmp_path / "vca_v2"
    project_name = "project-001"
    _write_startup_receipt(engine_root, project_name)
    _write_final_report_gate(engine_root, project_name)
    _write_report_metadata(
        engine_root,
        project_name,
        [
            {
                "candidate_id": "candidate-kept",
                "image_id": "image-001",
                "concept_family": "crack",
                "hybrid_descriptor": "line surface",
                "terminal_status": "kept",
                "citations": [
                    {
                        "citation_id": "citation-001",
                        "status": "exported",
                        "source_citation": "example.pdf",
                        "page_number": 12,
                        "score": 0.8,
                    }
                ],
                "bbox": {"x_min": 10.0, "y_min": 20.0, "x_max": 30.0, "y_max": 40.0},
            }
        ],
    )

    # When: the report is loaded for this run.
    artifacts = load_vca_report(engine_root, project_name, is_dry_run=False)

    # Then: the finding carries the candidate's real evidence, not a generic template.
    assert len(artifacts.findings) == 1
    finding = artifacts.findings[0]
    assert finding.category == "VCA_ANOMALY"
    assert finding.candidate_id == "candidate-kept"
    assert finding.image_id == "image-001"
    assert finding.concept_family == "crack"
    assert finding.descriptor == "line surface"
    assert len(finding.citations) == 1
    assert finding.citations[0].citation_id == "citation-001"
    assert finding.citations[0].source_citation == "example.pdf"
    assert finding.citations[0].page_number == 12
    assert finding.bbox is not None
    assert (finding.bbox.x_min, finding.bbox.y_min) == (10.0, 20.0)
    assert (finding.bbox.x_max, finding.bbox.y_max) == (30.0, 40.0)


def test_load_vca_report_omits_non_exported_citations(tmp_path: Path) -> None:
    # Given: a kept candidate whose only citation never resolved to a real page.
    engine_root = tmp_path / "vca_v2"
    project_name = "project-002"
    _write_startup_receipt(engine_root, project_name)
    _write_final_report_gate(engine_root, project_name)
    _write_report_metadata(
        engine_root,
        project_name,
        [
            {
                "candidate_id": "candidate-kept",
                "image_id": "image-001",
                "concept_family": "crack",
                "hybrid_descriptor": "line surface",
                "terminal_status": "kept",
                "citations": [
                    {"citation_id": "citation-001", "status": "non_exportable_corpus_citation"}
                ],
            }
        ],
    )

    # When: the report is loaded for this run.
    artifacts = load_vca_report(engine_root, project_name, is_dry_run=False)

    # Then: the finding has no citations rather than a fabricated one.
    assert artifacts.findings[0].citations == ()
    assert artifacts.findings[0].bbox is None


def test_load_vca_report_keeps_top_two_citations_by_score(tmp_path: Path) -> None:
    # Given: a kept candidate with three exported citations of differing scores.
    engine_root = tmp_path / "vca_v2"
    project_name = "project-003"
    _write_startup_receipt(engine_root, project_name)
    _write_final_report_gate(engine_root, project_name)
    _write_report_metadata(
        engine_root,
        project_name,
        [
            {
                "candidate_id": "candidate-kept",
                "image_id": "image-001",
                "concept_family": "crack",
                "hybrid_descriptor": "line surface",
                "terminal_status": "kept",
                "citations": [
                    {
                        "citation_id": "citation-low",
                        "status": "exported",
                        "source_citation": "low.pdf",
                        "page_number": 1,
                        "score": 0.1,
                    },
                    {
                        "citation_id": "citation-high",
                        "status": "exported",
                        "source_citation": "high.pdf",
                        "page_number": 2,
                        "score": 0.9,
                    },
                    {
                        "citation_id": "citation-mid",
                        "status": "exported",
                        "source_citation": "mid.pdf",
                        "page_number": 3,
                        "score": 0.5,
                    },
                ],
            }
        ],
    )

    # When: the report is loaded for this run.
    artifacts = load_vca_report(engine_root, project_name, is_dry_run=False)

    # Then: only the top two citations by score survive, highest first.
    citation_ids = tuple(c.citation_id for c in artifacts.findings[0].citations)
    assert citation_ids == ("citation-high", "citation-mid")


def test_load_vca_report_excludes_suppressed_candidates(tmp_path: Path) -> None:
    # Given: a candidate that relation authority suppressed as a duplicate.
    engine_root = tmp_path / "vca_v2"
    project_name = "project-004"
    _write_startup_receipt(engine_root, project_name)
    _write_final_report_gate(engine_root, project_name)
    _write_report_metadata(
        engine_root,
        project_name,
        [
            {
                "candidate_id": "candidate-suppressed",
                "image_id": "image-001",
                "concept_family": "crack",
                "hybrid_descriptor": "line surface",
                "terminal_status": "suppressed",
                "citations": [],
            }
        ],
    )

    # When: the report is loaded for this run.
    artifacts = load_vca_report(engine_root, project_name, is_dry_run=False)

    # Then: no candidate-derived finding is produced; availability is reported instead.
    assert len(artifacts.findings) == 1
    assert artifacts.findings[0].category == "VCA_REPORT"
    assert artifacts.findings[0].candidate_id is None
