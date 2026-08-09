from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from modules.orchestration.stage_execution import ProjectStageRequest
from modules.orchestration.stage_paths import StagePathMap
from modules.rag.corpus.corpus import (
    CorpusDocumentStatus,
    CorpusMetadataRow,
    CorpusPageText,
)
from modules.rag.operations.candidate_sidecars import (
    RAG_CANDIDATE_EVIDENCE_SIDECAR,
    RAG_CANDIDATE_SIDECAR_MANIFEST,
    RAG_VISUAL_CONCEPT_CARDS_SIDECAR,
)
from modules.rag.qwen import QwenBridgeCandidateArtifact, write_qwen_bridge_results
from modules.rag.qwen.qwen_bridge_json import parse_json_object
from modules.rag.retrieval.vector_index import InMemoryTextEmbedder
from modules.rag.startup_runner import run_rag_stage
from modules.shared import CandidateId, ExitCode, QwenBridgeResult, QwenBridgeStatus

if TYPE_CHECKING:
    from pathlib import Path

    from modules.rag.corpus.document_corpus import (
        DocumentCorpusConfig,
        DocumentTextExtractor,
    )


def _stage_paths(tmp_path: Path) -> StagePathMap:
    return StagePathMap(
        preprocessing=tmp_path / "preprocessing",
        rough_masking=tmp_path / "rough_masking",
        visual_cue_generation=tmp_path / "visual_cue_generation",
        rag=tmp_path / "rag",
        prompt_generating=tmp_path / "prompt_generating",
        mask_refining=tmp_path / "mask_refining",
        anomaly_grouping=tmp_path / "anomaly_grouping",
        report_generating=tmp_path / "report_generating",
    )


def _request(tmp_path: Path, *, dry_run: bool = False) -> ProjectStageRequest:
    return ProjectStageRequest(
        project_name="rag-startup",
        stage_name="rag",
        paths=_stage_paths(tmp_path),
        device="cpu",
        model_cache_root=tmp_path / "models",
        dry_run=dry_run,
        verify_model_hashes=True,
    )


def _write_jsonl(
    path: Path,
    rows: tuple[dict[str, str | int | float | list[str]], ...],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


def _write_rough_inputs(paths: StagePathMap) -> None:
    records_path = paths.rough_masking / "lane-a" / "object-001" / "owlv2_sam2"
    records_path.mkdir(parents=True)
    _ = (records_path / "records.json").write_text(
        json.dumps(
            [
                {
                    "accepted": True,
                    "candidate_id": "candidate-001",
                    "image": "image-001",
                    "prompt": "white deposit on rim",
                }
            ],
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _write_required_inputs(paths: StagePathMap) -> None:
    _write_rough_inputs(paths)
    _write_jsonl(
        paths.rag / "queries.jsonl",
        (
            {
                "lane": "lane-a",
                "prompt_text": "white deposit on rim",
                "query_id": "lane-a:prompt-01",
            },
        ),
    )
    _write_jsonl(
        paths.rag / "prompt_rag_results.jsonl",
        (
            {
                "chunk_id": "chunk-001",
                "citation_id": "citation-001",
                "lane": "lane-a",
                "matched_terms": ["white", "deposit"],
                "prompt_text": "white deposit on rim",
                "query_id": "lane-a:prompt-01",
                "rank": 1,
                "score": 7.5,
                "snippet_text": "white powder accretion on a ceramic rim",
            },
        ),
    )


def _corpus_row() -> CorpusMetadataRow:
    return CorpusMetadataRow(
        document_id="stone-conservation",
        relative_path="stone.pdf",
        status=CorpusDocumentStatus.INCLUDED_TEXT_PDF.value,
        text="White powdery deposit on the stone surface.",
        pages=(
            CorpusPageText(
                page_number=1,
                text="White powdery deposit on the stone surface.",
            ),
        ),
    )


def _install_fake_corpus(
    monkeypatch: pytest.MonkeyPatch,
    rows: tuple[CorpusMetadataRow, ...],
) -> None:
    def build_corpus(
        config: DocumentCorpusConfig,
        extractor: DocumentTextExtractor,
    ) -> tuple[CorpusMetadataRow, ...]:
        _ = config, extractor
        return rows

    monkeypatch.setattr(
        "modules.rag.startup_corpus_cache.build_document_corpus",
        build_corpus,
        raising=False,
    )


def _install_fake_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    embedder = InMemoryTextEmbedder(
        passage_vectors={
            "White powdery deposit on the stone surface.": (1.0, 0.0),
        },
        query_vectors={
            # Retrieval queries now include the fixed EN->KO mapping-table
            # terms alongside the English prompt tokens (stopwords dropped).
            "흰색 오염물 퇴적물 white deposit rim": (1.0, 0.0),
            # Candidates with a successful Qwen bridge result append their own
            # sorted, deduplicated query-driving terms (qwen_query_signature)
            # after the bilingual base text.
            "흰색 오염물 퇴적물 white deposit rim powdery white powder": (1.0, 0.0),
            "흰색 오염물 퇴적물 white deposit rim gray patch smooth": (1.0, 0.0),
        },
        model_id="test-embedder",
    )

    def fake_embedder(model_cache_root: Path, device: str) -> InMemoryTextEmbedder:
        _ = model_cache_root, device
        return embedder

    monkeypatch.setattr(
        "modules.rag.startup_runner._startup_embedder",
        fake_embedder,
        raising=False,
    )


def _write_qwen_inputs(paths: StagePathMap) -> None:
    _ = write_qwen_bridge_results(
        paths.rag,
        (
            QwenBridgeCandidateArtifact(
                "lane-a/object-001/owlv2_sam2/records.json",
                0,
                QwenBridgeResult(
                    candidate_id=CandidateId("candidate-001"),
                    status=QwenBridgeStatus.SUCCESS,
                    selected_terms=("white powder",),
                    extracted_descriptors=("powdery",),
                    confidence=0.91,
                    reason="explicit bridge result",
                    qwen_observation_id="qwen-001",
                    input_view_hashes=("a" * 64,),
                ),
            ),
        ),
    )


# lane-a:object-001과 lane-a:object-002 둘 다 같은 seed 프롬프트("white deposit
# on rim")를 쓰지만 서로 다른 후보(candidate-001/candidate-002)다. 아래
# "다른 Qwen 서술어는 분리" 테스트와 "Qwen 없으면 합쳐짐" 테스트가 공유한다.
def _write_two_rough_candidates(paths: StagePathMap) -> None:
    for object_name, candidate_id, image_id in (
        ("object-001", "candidate-001", "image-001"),
        ("object-002", "candidate-002", "image-002"),
    ):
        records_path = paths.rough_masking / "lane-a" / object_name / "owlv2_sam2"
        records_path.mkdir(parents=True)
        _ = (records_path / "records.json").write_text(
            json.dumps(
                [
                    {
                        "accepted": True,
                        "candidate_id": candidate_id,
                        "image": image_id,
                        "prompt": "white deposit on rim",
                    }
                ],
                sort_keys=True,
            ),
            encoding="utf-8",
        )


def test_run_rag_stage_dry_run_writes_no_sidecars(tmp_path: Path) -> None:
    # Given: a dry-run startup RAG request with no real artifacts.
    request = _request(tmp_path, dry_run=True)

    # When: the stage runner executes.
    exit_code = run_rag_stage(request)

    # Then: it succeeds without producing real sidecars.
    assert exit_code == int(ExitCode.OK)
    assert not (request.paths.rag / RAG_CANDIDATE_EVIDENCE_SIDECAR).exists()
    assert not (request.paths.rag / RAG_VISUAL_CONCEPT_CARDS_SIDECAR).exists()
    assert not (request.paths.rag / RAG_CANDIDATE_SIDECAR_MANIFEST).exists()


@pytest.mark.parametrize(
    "missing_path",
    [
        "rough_root",
    ],
)
def test_run_rag_stage_fails_when_required_artifact_is_missing(
    tmp_path: Path,
    missing_path: str,
) -> None:
    # Given: startup RAG inputs with one required upstream artifact absent.
    request = _request(tmp_path)
    _write_required_inputs(request.paths)
    if missing_path == "rough_root":
        records_path = request.paths.rough_masking / "lane-a/object-001/owlv2_sam2"
        (records_path / "records.json").unlink()
    # When: the stage runner executes.
    exit_code = run_rag_stage(request)

    # Then: it fails closed without pretending sidecars are complete.
    assert exit_code == int(ExitCode.INCOMPLETE_OR_FAILURE)


def test_run_rag_stage_fails_when_local_corpus_index_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: startup must generate retrieval artifacts but no local corpus is indexed.
    request = _request(tmp_path)
    _write_rough_inputs(request.paths)
    _install_fake_corpus(monkeypatch, ())

    # When: the stage runner executes.
    exit_code = run_rag_stage(request)

    # Then: it fails closed instead of inventing retrieval or depending on Qwen.
    assert exit_code == int(ExitCode.INCOMPLETE_OR_FAILURE)
    assert not (request.paths.rag / RAG_CANDIDATE_EVIDENCE_SIDECAR).exists()
    assert not (request.paths.rag / RAG_VISUAL_CONCEPT_CARDS_SIDECAR).exists()


def test_run_rag_stage_writes_candidate_sidecars_without_qwen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: real rough artifacts without visual-cue bridge artifacts.
    request = _request(tmp_path)
    _write_rough_inputs(request.paths)
    _install_fake_corpus(monkeypatch, (_corpus_row(),))
    _install_fake_embedder(monkeypatch)

    # When: the stage runner executes.
    exit_code = run_rag_stage(request)

    # Then: deterministic RAG sidecars are written under the RAG project root.
    assert exit_code == int(ExitCode.OK)
    evidence_rows = (request.paths.rag / RAG_CANDIDATE_EVIDENCE_SIDECAR).read_text(
        encoding="utf-8"
    ).splitlines()
    card_rows = (request.paths.rag / RAG_VISUAL_CONCEPT_CARDS_SIDECAR).read_text(
        encoding="utf-8"
    ).splitlines()
    manifest = (request.paths.rag / RAG_CANDIDATE_SIDECAR_MANIFEST).read_text(
        encoding="utf-8"
    )
    assert len(evidence_rows) == 1
    assert len(card_rows) == 1
    assert manifest == (
        '{"rag_candidate_evidence_rows":1,"rag_visual_concept_cards":1,'
        '"schema":"rag_candidate_evidence_v1"}'
    )


def test_run_rag_stage_uses_qwen_bridge_when_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: real rough and optional visual-cue bridge artifacts.
    request = _request(tmp_path)
    _write_rough_inputs(request.paths)
    _write_qwen_inputs(request.paths)
    _install_fake_corpus(monkeypatch, (_corpus_row(),))
    _install_fake_embedder(monkeypatch)

    # When: the stage runner executes.
    exit_code = run_rag_stage(request)

    # Then: Qwen-derived safe terms enrich the concept card.
    assert exit_code == int(ExitCode.OK)
    card_rows = (request.paths.rag / RAG_VISUAL_CONCEPT_CARDS_SIDECAR).read_text(
        encoding="utf-8"
    ).splitlines()
    card = parse_json_object(card_rows[0])
    assert set(card["descriptor_terms"]) == {"white", "powder", "powdery"}


def test_run_rag_stage_gives_distinct_qwen_candidates_separate_queries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: two candidates share the same (lane, prompt_text) seed but have
    # Qwen bridge results with different query-driving terms.
    request = _request(tmp_path)
    _write_two_rough_candidates(request.paths)
    _ = write_qwen_bridge_results(
        request.paths.rag,
        (
            QwenBridgeCandidateArtifact(
                "lane-a/object-001/owlv2_sam2/records.json",
                0,
                QwenBridgeResult(
                    candidate_id=CandidateId("candidate-001"),
                    status=QwenBridgeStatus.SUCCESS,
                    selected_terms=("white powder",),
                    extracted_descriptors=("powdery",),
                    confidence=0.91,
                    reason="first candidate bridge result",
                    qwen_observation_id="qwen-001",
                    input_view_hashes=("a" * 64,),
                ),
            ),
            QwenBridgeCandidateArtifact(
                "lane-a/object-002/owlv2_sam2/records.json",
                0,
                QwenBridgeResult(
                    candidate_id=CandidateId("candidate-002"),
                    status=QwenBridgeStatus.SUCCESS,
                    selected_terms=("gray patch",),
                    extracted_descriptors=("smooth",),
                    confidence=0.88,
                    reason="second candidate bridge result",
                    qwen_observation_id="qwen-002",
                    input_view_hashes=("b" * 64,),
                ),
            ),
        ),
    )
    _install_fake_corpus(monkeypatch, (_corpus_row(),))
    _install_fake_embedder(monkeypatch)

    # When: the stage runner executes.
    exit_code = run_rag_stage(request)

    # Then: each candidate's own Qwen terms drive a distinct RAG query, instead
    # of both collapsing into a single project-wide query for the shared prompt.
    assert exit_code == int(ExitCode.OK)
    queries = [
        parse_json_object(line)
        for line in (request.paths.rag / "queries.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    same_prompt_queries = [
        query
        for query in queries
        if query["lane"] == "lane-a" and query["prompt_text"] == "white deposit on rim"
    ]
    assert len(same_prompt_queries) == 2
    query_ids = {query["query_id"] for query in same_prompt_queries}
    assert len(query_ids) == 2
    evidence_rows = [
        parse_json_object(line)
        for line in (request.paths.rag / RAG_CANDIDATE_EVIDENCE_SIDECAR).read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert len(evidence_rows) == 2
    assert {row["query_id"] for row in evidence_rows} == query_ids


def test_run_rag_stage_collapses_candidates_without_qwen_terms_into_shared_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: two candidates share the same (lane, prompt_text) seed and neither
    # has any Qwen bridge data at all (skip mode / not yet run).
    request = _request(tmp_path)
    _write_two_rough_candidates(request.paths)
    _install_fake_corpus(monkeypatch, (_corpus_row(),))
    _install_fake_embedder(monkeypatch)

    # When: the stage runner executes.
    exit_code = run_rag_stage(request)

    # Then: today's exact fallback behavior is preserved - both candidates
    # collapse into the one shared query for their common seed prompt.
    assert exit_code == int(ExitCode.OK)
    queries = [
        parse_json_object(line)
        for line in (request.paths.rag / "queries.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    same_prompt_queries = [
        query
        for query in queries
        if query["lane"] == "lane-a" and query["prompt_text"] == "white deposit on rim"
    ]
    assert len(same_prompt_queries) == 1
    evidence_rows = [
        parse_json_object(line)
        for line in (request.paths.rag / RAG_CANDIDATE_EVIDENCE_SIDECAR).read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert len(evidence_rows) == 2
    assert len({row["query_id"] for row in evidence_rows}) == 1
