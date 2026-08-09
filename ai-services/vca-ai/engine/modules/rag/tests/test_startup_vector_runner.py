from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from modules.orchestration.stage_execution import ProjectStageRequest
from modules.orchestration.stage_paths import StagePathMap
from modules.rag.corpus.corpus import (
    CorpusDocumentStatus,
    CorpusMetadataRow,
    CorpusPageText,
)
from modules.rag.operations.candidate_sidecars import RAG_VISUAL_CONCEPT_CARDS_SIDECAR
from modules.rag.qwen.qwen_bridge_json import parse_json_object
from modules.rag.retrieval.vector_index import (
    FloatMatrix,
    FloatVector,
    InMemoryTextEmbedder,
)
from modules.rag.startup_runner import run_rag_stage
from modules.shared import ExitCode

if TYPE_CHECKING:
    import pytest

    from modules.rag.corpus.document_corpus import (
        DocumentCorpusConfig,
        DocumentTextExtractor,
    )


def _request(tmp_path: Path) -> ProjectStageRequest:
    paths = StagePathMap(
        preprocessing=tmp_path / "preprocessing",
        rough_masking=tmp_path / "rough_masking",
        visual_cue_generation=tmp_path / "visual_cue_generation",
        rag=tmp_path / "rag",
        prompt_generating=tmp_path / "prompt_generating",
        mask_refining=tmp_path / "mask_refining",
        anomaly_grouping=tmp_path / "anomaly_grouping",
        report_generating=tmp_path / "report_generating",
    )
    return ProjectStageRequest(
        project_name="rag-startup",
        stage_name="rag",
        paths=paths,
        device="cpu",
        model_cache_root=tmp_path / "models",
        dry_run=False,
        verify_model_hashes=True,
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


def _write_stale_retrieval_artifacts(paths: StagePathMap) -> None:
    paths.rag.mkdir(parents=True)
    _ = (paths.rag / "queries.jsonl").write_text(
        json.dumps(
            {
                "lane": "lane-a",
                "prompt_text": "stale prompt",
                "query_id": "stale-query",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    _ = (paths.rag / "prompt_rag_results.jsonl").write_text(
        json.dumps(
            {
                "chunk_id": "stale-chunk",
                "citation_id": "stale-citation",
                "lane": "lane-a",
                "matched_terms": ["stale"],
                "prompt_text": "stale prompt",
                "query_id": "stale-query",
                "rank": 1,
                "score": 0.1,
                "source_citation": "stale.pdf",
                "page_number": 1,
                "snippet_text": "stale cached retrieval",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _install_fake_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    def build_corpus(
        config: DocumentCorpusConfig,
        extractor: DocumentTextExtractor,
    ) -> tuple[CorpusMetadataRow, ...]:
        _ = config, extractor
        return (
            CorpusMetadataRow(
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
            ),
        )

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


@dataclass
class _SpyEmbedder:
    """Wraps a real embedder to record corpus re-embedding calls."""

    inner: InMemoryTextEmbedder
    passage_embed_call_count: int = field(default=0)

    @property
    def model_id(self) -> str:
        return self.inner.model_id

    def embed_passages(self, texts: tuple[str, ...]) -> FloatMatrix:
        self.passage_embed_call_count += 1
        return self.inner.embed_passages(texts)

    def embed_query(self, text: str) -> FloatVector:
        return self.inner.embed_query(text)


def _install_spy_embedder(monkeypatch: pytest.MonkeyPatch) -> _SpyEmbedder:
    spy = _SpyEmbedder(
        InMemoryTextEmbedder(
            passage_vectors={
                "White powdery deposit on the stone surface.": (1.0, 0.0),
            },
            query_vectors={
                "흰색 오염물 퇴적물 white deposit rim": (1.0, 0.0),
            },
            model_id="test-embedder",
        )
    )

    def fake_embedder(model_cache_root: Path, device: str) -> _SpyEmbedder:
        _ = model_cache_root, device
        return spy

    monkeypatch.setattr(
        "modules.rag.startup_runner._startup_embedder",
        fake_embedder,
        raising=False,
    )
    return spy


def test_run_rag_stage_materializes_vector_index_and_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: rough records exist but startup retrieval artifacts are absent.
    request = _request(tmp_path)
    _write_rough_inputs(request.paths)
    _install_fake_corpus(monkeypatch)
    _install_fake_embedder(monkeypatch)

    # When: the stage runner executes.
    exit_code = run_rag_stage(request)

    # Then: vector retrieval artifacts are materialized before sidecars.
    assert exit_code == int(ExitCode.OK)
    queries = (request.paths.rag / "queries.jsonl").read_text(encoding="utf-8")
    results = (request.paths.rag / "prompt_rag_results.jsonl").read_text(
        encoding="utf-8"
    )
    card_rows = (request.paths.rag / RAG_VISUAL_CONCEPT_CARDS_SIDECAR).read_text(
        encoding="utf-8"
    ).splitlines()
    result_row = parse_json_object(results.splitlines()[0])
    assert '"prompt_text":"white deposit on rim"' in queries
    assert result_row["source_citation"] == "stone.pdf"
    assert result_row["page_number"] == 1
    assert len(card_rows) == 1
    assert (request.model_cache_root / "rag/vector_index/embeddings.npy").is_file()
    assert (request.model_cache_root / "rag/vector_index/chunks.jsonl").is_file()
    assert (request.model_cache_root / "rag/vector_index/manifest.json").is_file()
    assert (request.model_cache_root / "rag/.materialize.lock").is_file()


def test_run_rag_stage_reuses_persisted_vector_index_on_second_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a first run has already embedded and persisted the corpus index.
    request = _request(tmp_path)
    _write_rough_inputs(request.paths)
    _install_fake_corpus(monkeypatch)
    spy = _install_spy_embedder(monkeypatch)
    first_exit_code = run_rag_stage(request)
    assert first_exit_code == int(ExitCode.OK)
    assert spy.passage_embed_call_count == 1

    # When: the stage runs again for the same unchanged corpus.
    second_exit_code = run_rag_stage(request)

    # Then: the persisted vector index is reused instead of re-embedding.
    assert second_exit_code == int(ExitCode.OK)
    assert spy.passage_embed_call_count == 1


def test_run_rag_stage_rebuilds_stale_retrieval_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: stale retrieval artifacts exist from an earlier run.
    request = _request(tmp_path)
    _write_rough_inputs(request.paths)
    _write_stale_retrieval_artifacts(request.paths)
    _install_fake_corpus(monkeypatch)
    _install_fake_embedder(monkeypatch)

    # When: the stage runner executes for the current source corpus.
    exit_code = run_rag_stage(request)

    # Then: retrieval artifacts are regenerated instead of reused as inputs.
    assert exit_code == int(ExitCode.OK)
    queries = (request.paths.rag / "queries.jsonl").read_text(encoding="utf-8")
    results = (request.paths.rag / "prompt_rag_results.jsonl").read_text(
        encoding="utf-8"
    )
    result_row = parse_json_object(results.splitlines()[0])
    assert "stale prompt" not in queries
    assert result_row["source_citation"] == "stone.pdf"
    assert result_row["snippet_text"] == "White powdery deposit on the stone surface."


def test_run_rag_stage_rejects_symlinked_retrieval_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the query artifact leaf is a symlink to an external file.
    request = _request(tmp_path)
    _write_rough_inputs(request.paths)
    request.paths.rag.mkdir(parents=True)
    external_file = tmp_path / "external-queries.jsonl"
    _ = external_file.write_text("sentinel\n", encoding="utf-8")
    (request.paths.rag / "queries.jsonl").symlink_to(external_file)
    _install_fake_corpus(monkeypatch)
    _install_fake_embedder(monkeypatch)

    # When: the stage runner tries to materialize fresh retrieval artifacts.
    exit_code = run_rag_stage(request)

    # Then: it fails closed before following the symlinked output leaf.
    assert exit_code == int(ExitCode.INCOMPLETE_OR_FAILURE)
    assert external_file.read_text(encoding="utf-8") == "sentinel\n"


def test_run_rag_stage_rejects_symlinked_rag_parent_before_mkdir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the RAG output parent is a symlink to an external directory.
    linked_parent = tmp_path / "linked-output"
    external_parent = tmp_path / "external-output"
    external_parent.mkdir()
    linked_parent.symlink_to(external_parent, target_is_directory=True)
    request = _request(tmp_path)
    paths = StagePathMap(
        preprocessing=request.paths.preprocessing,
        rough_masking=request.paths.rough_masking,
        visual_cue_generation=request.paths.visual_cue_generation,
        rag=linked_parent / "rag",
        prompt_generating=request.paths.prompt_generating,
        mask_refining=request.paths.mask_refining,
        anomaly_grouping=request.paths.anomaly_grouping,
        report_generating=request.paths.report_generating,
    )
    request = ProjectStageRequest(
        project_name=request.project_name,
        stage_name=request.stage_name,
        paths=paths,
        device=request.device,
        model_cache_root=request.model_cache_root,
        dry_run=request.dry_run,
        verify_model_hashes=request.verify_model_hashes,
    )
    _write_rough_inputs(request.paths)
    _install_fake_corpus(monkeypatch)
    _install_fake_embedder(monkeypatch)

    # When: startup tries to create fresh retrieval outputs.
    exit_code = run_rag_stage(request)

    # Then: it fails before creating directories through the symlinked parent.
    assert exit_code == int(ExitCode.INCOMPLETE_OR_FAILURE)
    assert not (external_parent / "rag").exists()


def test_startup_retrieval_temp_file_is_removed_after_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: replacing the startup query artifact fails after temp creation.
    request = _request(tmp_path)
    _write_rough_inputs(request.paths)
    _install_fake_corpus(monkeypatch)
    _install_fake_embedder(monkeypatch)
    original_replace = Path.replace

    def fail_query_replace(self: Path, target: str | Path) -> Path:
        if self.name == "queries.jsonl.tmp":
            reason = "replace failed"
            raise OSError(reason)
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_query_replace)

    # When: the stage runner attempts to publish retrieval artifacts.
    exit_code = run_rag_stage(request)

    # Then: the failed atomic write cleans up its temp artifact.
    assert exit_code == int(ExitCode.INCOMPLETE_OR_FAILURE)
    assert not (request.paths.rag / "queries.jsonl.tmp").exists()
