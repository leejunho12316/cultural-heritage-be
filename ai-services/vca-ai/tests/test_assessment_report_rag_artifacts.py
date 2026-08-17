import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.main import app


client = TestClient(app)


def write_report_artifacts(
    engine_root: Path,
    project_name: str,
    *,
    verification_status: str = "pass",
) -> None:
    startup_path = engine_root / "output" / "result" / project_name / "receipts" / "startup.json"
    final_report_root = (
        engine_root / "output" / "report_generating" / project_name / "final_report"
    )
    startup_path.parent.mkdir(parents=True)
    final_report_root.joinpath("verification").mkdir(parents=True)
    startup_path.write_text(
        json.dumps({"project_name": project_name, "status": "completed", "stages": []}),
        encoding="utf-8",
    )
    final_report_root.joinpath("metadata.json").write_text(
        json.dumps({"schema": "raw-image-final-report-v1", "final_success": True}),
        encoding="utf-8",
    )
    final_report_root.joinpath("verification", "receipt.json").write_text(
        json.dumps(
            {
                "schema": "raw-image-final-report-static-v1",
                "verification_status": verification_status,
            }
        ),
        encoding="utf-8",
    )


def write_rag_artifacts(engine_root: Path, project_name: str) -> None:
    rag_root = engine_root / "output" / "rag" / project_name
    rag_root.mkdir(parents=True)
    rag_root.joinpath("rag_candidate_sidecars_manifest.json").write_text(
        json.dumps(
            {
                "schema": "rag_candidate_evidence_v1",
                "rag_candidate_evidence_rows": 1,
                "rag_visual_concept_cards": 1,
            }
        ),
        encoding="utf-8",
    )
    rag_root.joinpath("queries.jsonl").write_text(
        json.dumps(
            {
                "lane": "owlv2_sam2",
                "prompt_text": "surface crack",
                "query_id": "owlv2_sam2:prompt-0001",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rag_root.joinpath("prompt_rag_results.jsonl").write_text(
        json.dumps(
            {
                "chunk_id": "doc-1:chunk-0001",
                "citation_id": "doc-1:chunk-0001:citation",
                "lane": "owlv2_sam2",
                "matched_terms": ["surface", "crack"],
                "page_number": 44,
                "prompt_text": "surface crack",
                "query_id": "owlv2_sam2:prompt-0001",
                "rank": 1,
                "score": 0.86,
                "snippet_text": "Crack evidence from a cited conservation source.",
                "source_citation": "conservation-basics.pdf",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rag_root.joinpath("rag_candidate_evidence.jsonl").write_text(
        json.dumps(
            {
                "schema": "rag_candidate_evidence_v1",
                "evidence_state": "rag_evidence_ready",
                "lane": "owlv2_sam2",
                "matched_citation_ids": ["doc-1:chunk-0001:citation"],
                "prompt_text": "surface crack",
                "query_id": "owlv2_sam2:prompt-0001",
                "rag_parent_candidate_id": "rough:candidate-1",
                "top_citation_id": "doc-1:chunk-0001:citation",
                "top_retrieval_score": 0.86,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rag_root.joinpath("rag_visual_concept_cards.jsonl").write_text(
        json.dumps(
            {
                "concept_card_id": "rag-card:1",
                "concept_family": "crack",
                "context_terms": ["surface"],
                "descriptor_terms": ["line"],
                "material_terms": [],
                "provenance_strength": "strong",
                "rag_parent_candidate_id": "rough:candidate-1",
                "raw_retrieved_sentence": "Cracks are clearly visible on the surface.",
                "retrieval_score": 0.86,
                "source_citation_ids": ["doc-1:chunk-0001:citation"],
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_assessment_report_when_rag_sidecars_exist_includes_rag_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a verified VCA report with generated RAG sidecars
    engine_root = tmp_path / "vca_v2"
    project_name = "artifact-123-run-123"
    write_report_artifacts(engine_root, project_name)
    write_rag_artifacts(engine_root, project_name)
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))

    # When: Spring retrieves the completed assessment report
    response = client.get(f"/internal/vca/assessment-runs/vca-artifact-123~{project_name}/report")

    # Then: generated RAG artifacts are exposed without changing existing report fields
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == "VCA report for artifact-123-run-123: verification pass."
    assert payload["ragArtifacts"] == {
        "schema": "rag_candidate_evidence_v1",
        "queryCount": 1,
        "retrievalResultCount": 1,
        "evidenceRowCount": 1,
        "visualConceptCardCount": 1,
        "queries": [
            {
                "lane": "owlv2_sam2",
                "promptText": "surface crack",
                "queryId": "owlv2_sam2:prompt-0001",
            }
        ],
        "retrievalResults": [
            {
                "chunkId": "doc-1:chunk-0001",
                "citationId": "doc-1:chunk-0001:citation",
                "lane": "owlv2_sam2",
                "matchedTerms": ["surface", "crack"],
                "pageNumber": 44,
                "promptText": "surface crack",
                "queryId": "owlv2_sam2:prompt-0001",
                "rank": 1,
                "score": 0.86,
                "snippetText": "Crack evidence from a cited conservation source.",
                "sourceCitation": "conservation-basics.pdf",
            }
        ],
        "evidenceRows": [
            {
                "evidenceState": "rag_evidence_ready",
                "lane": "owlv2_sam2",
                "matchedCitationIds": ["doc-1:chunk-0001:citation"],
                "promptText": "surface crack",
                "queryId": "owlv2_sam2:prompt-0001",
                "ragParentCandidateId": "rough:candidate-1",
                "topCitationId": "doc-1:chunk-0001:citation",
                "topRetrievalScore": 0.86,
            }
        ],
        "visualConceptCards": [
            {
                "conceptCardId": "rag-card:1",
                "conceptFamily": "crack",
                "contextTerms": ["surface"],
                "descriptorTerms": ["line"],
                "materialTerms": [],
                "provenanceStrength": "strong",
                "ragParentCandidateId": "rough:candidate-1",
                "rawRetrievedSentence": "Cracks are clearly visible on the surface.",
                "retrievalScore": 0.86,
                "sourceCitationIds": ["doc-1:chunk-0001:citation"],
            }
        ],
    }


def test_assessment_report_when_final_verification_fails_does_not_expose_rag_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: RAG sidecars exist but final report verification failed
    engine_root = tmp_path / "vca_v2"
    project_name = "artifact-123-run-123"
    write_report_artifacts(engine_root, project_name, verification_status="fail")
    write_rag_artifacts(engine_root, project_name)
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))

    # When: Spring retrieves the report
    response = client.get(f"/internal/vca/assessment-runs/vca-artifact-123~{project_name}/report")

    # Then: final verification remains the required gate before RAG exposure
    assert response.status_code == 502
    assert "verification did not pass" in response.json()["detail"]


def test_assessment_report_when_rag_sidecar_is_invalid_returns_502(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a verified report with a malformed generated RAG sidecar
    engine_root = tmp_path / "vca_v2"
    project_name = "artifact-123-run-123"
    write_report_artifacts(engine_root, project_name)
    write_rag_artifacts(engine_root, project_name)
    rag_root = engine_root / "output" / "rag" / project_name
    rag_root.joinpath("rag_candidate_evidence.jsonl").write_text(
        "not-json\n", encoding="utf-8"
    )
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))

    # When: Spring retrieves the report
    response = client.get(f"/internal/vca/assessment-runs/vca-artifact-123~{project_name}/report")

    # Then: invalid generated RAG sidecars fail closed with a named artifact
    assert response.status_code == 502
    assert "RAG candidate evidence" in response.json()["detail"]


def test_assessment_report_when_rag_producer_emits_nullable_fields_keeps_nulls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: generated RAG sidecars contain nullable fields emitted by the producer
    engine_root = tmp_path / "vca_v2"
    project_name = "artifact-123-run-123"
    write_report_artifacts(engine_root, project_name)
    write_rag_artifacts(engine_root, project_name)
    rag_root = engine_root / "output" / "rag" / project_name
    rag_root.joinpath("prompt_rag_results.jsonl").write_text(
        json.dumps(
            {
                "chunk_id": "doc-1:chunk-0001",
                "citation_id": "doc-1:chunk-0001:citation",
                "lane": "owlv2_sam2",
                "matched_terms": ["surface", "crack"],
                "page_number": None,
                "prompt_text": "surface crack",
                "query_id": "owlv2_sam2:prompt-0001",
                "rank": 1,
                "score": 0.86,
                "snippet_text": "Crack evidence from a cited conservation source.",
                "source_citation": "conservation-basics.pdf",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rag_root.joinpath("rag_candidate_evidence.jsonl").write_text(
        json.dumps(
            {
                "schema": "rag_candidate_evidence_v1",
                "evidence_state": "rag_evidence_not_found",
                "lane": "owlv2_sam2",
                "matched_citation_ids": [],
                "prompt_text": "surface crack",
                "query_id": None,
                "rag_parent_candidate_id": "rough:candidate-1",
                "top_citation_id": None,
                "top_retrieval_score": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rag_root.joinpath("rag_visual_concept_cards.jsonl").write_text(
        json.dumps(
            {
                "concept_card_id": "rag-card:1",
                "concept_family": None,
                "context_terms": ["surface"],
                "descriptor_terms": ["line"],
                "material_terms": [],
                "provenance_strength": "weak",
                "rag_parent_candidate_id": "rough:candidate-1",
                "raw_retrieved_sentence": "Cracks are visible on the surface.",
                "retrieval_score": 0.52,
                "source_citation_ids": ["doc-1:chunk-0001:citation"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))

    # When: the report API serializes the completed report
    response = client.get(f"/internal/vca/assessment-runs/vca-artifact-123~{project_name}/report")

    # Then: nullable producer values remain explicit nulls instead of being dropped
    assert response.status_code == 200
    payload = response.json()["ragArtifacts"]
    assert payload["retrievalResults"][0]["pageNumber"] is None
    assert payload["evidenceRows"][0]["queryId"] is None
    assert payload["evidenceRows"][0]["topCitationId"] is None
    assert payload["evidenceRows"][0]["topRetrievalScore"] is None
    assert payload["visualConceptCards"][0]["conceptFamily"] is None


def test_assessment_report_when_manifest_count_mismatches_jsonl_rows_returns_502(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a manifest declares a different evidence row count than the sidecar file
    engine_root = tmp_path / "vca_v2"
    project_name = "artifact-123-run-123"
    write_report_artifacts(engine_root, project_name)
    write_rag_artifacts(engine_root, project_name)
    rag_root = engine_root / "output" / "rag" / project_name
    rag_root.joinpath("rag_candidate_sidecars_manifest.json").write_text(
        json.dumps(
            {
                "schema": "rag_candidate_evidence_v1",
                "rag_candidate_evidence_rows": 2,
                "rag_visual_concept_cards": 1,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VCA_ENGINE_ROOT", str(engine_root))

    # When: Spring retrieves the report
    response = client.get(f"/internal/vca/assessment-runs/vca-artifact-123~{project_name}/report")

    # Then: mismatched generated sidecars fail closed instead of reporting false counts
    assert response.status_code == 502
    assert "RAG sidecar manifest" in response.json()["detail"]
