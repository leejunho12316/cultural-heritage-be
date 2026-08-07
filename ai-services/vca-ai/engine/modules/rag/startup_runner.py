"""Project-level startup runner for candidate RAG sidecar generation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from modules.rag.corpus.corpus import Corpus
from modules.rag.corpus.document_index import (
    build_document_index,
)
from modules.rag.operations.candidate_sidecar_artifacts import (
    PromptQueryRecord,
    read_rough_records,
)
from modules.rag.operations.candidate_sidecars import (
    CandidateRagSidecarInputs,
    build_candidate_rag_sidecars,
    write_candidate_rag_sidecars,
)
from modules.rag.qwen.qwen_bridge_artifacts import qwen_bridge_results_path
from modules.rag.retrieval.vector_index import (
    TextEmbedder,
    build_vector_index,
    vector_retrieve,
)
from modules.rag.retrieval.vector_store import write_vector_index_artifacts
from modules.rag.startup_corpus_cache import (
    startup_corpus_rows,
    startup_document_source_root,
)
from modules.shared import (
    ContractValidationError,
    ExitCode,
    PathSafetyError,
    ensure_contained_write_path,
    ensure_no_symlink_leaf,
    ensure_no_symlink_path_components,
    ensure_source_document_is_not_write_target,
)

if TYPE_CHECKING:
    from pathlib import Path

    from modules.orchestration.stage_paths import StagePathMap
    from modules.rag.operations.candidate_sidecar_artifacts import RoughRagCandidate
    from modules.rag.retrieval.retrieval import RetrievalSnippet

type RetrievalArtifactRow = dict[str, str | int | float | list[str] | None]


class _RagStageRequest(Protocol):
    @property
    def paths(self) -> StagePathMap: ...

    @property
    def dry_run(self) -> bool: ...

    @property
    def model_cache_root(self) -> Path: ...


@dataclass(frozen=True, slots=True)
class _WriteBoundary:
    root: Path
    source_root: Path


MAX_SNIPPET_CHARS: Final = 2000
TOP_K_PER_PROMPT: Final = 5
QUERY_STOPWORDS: Final = frozenset({"a", "an", "of", "on", "or", "the"})
PROMPT_CUE_TERMS: Final = {
    "crack": ("line",),
    "crusty": ("crust",),
    "dark": ("black",),
    "spalled": ("flaking", "broad"),
    "spalling": ("flaking", "broad"),
}


def run_rag_stage(request: _RagStageRequest) -> int:
    """Build startup RAG sidecars from existing rough and retrieval artifacts."""
    if request.dry_run:
        return int(ExitCode.OK)
    try:
        _require_inputs(request.paths)
        _materialize_missing_retrieval_artifacts(request)
        result = build_candidate_rag_sidecars(
            CandidateRagSidecarInputs(
                rough_records_root=request.paths.rough_masking,
                queries_path=request.paths.rag / "queries.jsonl",
                prompt_rag_results_path=request.paths.rag / "prompt_rag_results.jsonl",
                qwen_bridge_results_path=_optional_qwen_bridge_path(
                    request.paths.rag
                ),
            )
        )
        write_candidate_rag_sidecars(request.paths.rag, result)
    except (ContractValidationError, OSError, PathSafetyError):
        return int(ExitCode.INCOMPLETE_OR_FAILURE)
    return int(ExitCode.OK)


def _require_inputs(paths: StagePathMap) -> None:
    _require_directory(paths.rough_masking, "rough_records_root")
    _require_rough_records(paths.rough_masking)


def _require_rough_records(path: Path) -> None:
    if not tuple(path.glob("**/records.json")):
        field = "rough_records_root"
        reason = "must contain records.json"
        raise ContractValidationError(field, reason)


def _require_directory(path: Path, field: str) -> None:
    if not path.is_dir():
        reason = "must reference a readable directory"
        raise ContractValidationError(field, reason)


def _materialize_missing_retrieval_artifacts(request: _RagStageRequest) -> None:
    queries_path = request.paths.rag / "queries.jsonl"
    results_path = request.paths.rag / "prompt_rag_results.jsonl"
    rough_records = read_rough_records(request.paths.rough_masking)
    queries = _prompt_queries(rough_records)
    rows = startup_corpus_rows(request.model_cache_root)
    index = build_document_index(Corpus.from_metadata(rows), MAX_SNIPPET_CHARS)
    if not index.chunks:
        field = "document_corpus"
        reason = "deterministic local corpus/index unavailable"
        raise ContractValidationError(field, reason)
    embedder = _startup_embedder(request.model_cache_root)
    vector_index = build_vector_index(index.chunks, embedder)
    write_vector_index_artifacts(request.model_cache_root, vector_index)
    result_rows: list[RetrievalArtifactRow] = []
    for query in queries:
        snippets = vector_retrieve(
            vector_index, query.prompt_text, embedder, top_k=TOP_K_PER_PROMPT
        )
        result_rows.extend(
            _result_row(query, rank, snippet, _evidence_terms(query.prompt_text))
            for rank, snippet in enumerate(snippets, start=1)
        )
    boundary = _WriteBoundary(request.paths.rag, startup_document_source_root())
    _guard_write_directory(request.paths.rag, boundary)
    request.paths.rag.mkdir(parents=True, exist_ok=True)
    _write_jsonl(queries_path, tuple(_query_row(query) for query in queries), boundary)
    _write_jsonl(results_path, tuple(result_rows), boundary)


def _prompt_queries(
    rough_records: tuple[RoughRagCandidate, ...],
) -> tuple[PromptQueryRecord, ...]:
    queries: list[PromptQueryRecord] = []
    seen: set[tuple[str, str]] = set()
    for rough in rough_records:
        key = (rough.lane, rough.prompt_text)
        if key in seen:
            continue
        seen.add(key)
        queries.append(
            PromptQueryRecord(
                lane=rough.lane,
                prompt_text=rough.prompt_text,
                query_id=f"{rough.lane}:prompt-{len(queries) + 1:04d}",
            )
        )
    return tuple(queries)


def _query_tokens(prompt_text: str) -> tuple[str, ...]:
    tokens = tuple(
        token.strip(".,:;()[]{}-_/").casefold()
        for token in prompt_text.split()
    )
    return tuple(
        dict.fromkeys(
            token for token in tokens if token and token not in QUERY_STOPWORDS
        )
    )


def _evidence_terms(prompt_text: str) -> tuple[str, ...]:
    terms: list[str] = []
    for token in _query_tokens(prompt_text):
        terms.append(token)
        terms.extend(PROMPT_CUE_TERMS.get(token, ()))
    return tuple(dict.fromkeys(terms))


def _query_row(query: PromptQueryRecord) -> RetrievalArtifactRow:
    return {
        "lane": query.lane,
        "prompt_text": query.prompt_text,
        "query_id": query.query_id,
    }


def _result_row(
    query: PromptQueryRecord,
    rank: int,
    snippet: RetrievalSnippet,
    evidence_terms: tuple[str, ...],
) -> RetrievalArtifactRow:
    return {
        "chunk_id": str(snippet.citation.chunk_id),
        "citation_id": str(snippet.citation.citation_id),
        "lane": query.lane,
        "matched_terms": list(dict.fromkeys((*evidence_terms, *snippet.matched_terms))),
        "prompt_text": query.prompt_text,
        "query_id": query.query_id,
        "rank": rank,
        "score": snippet.score,
        "source_citation": snippet.citation.source_citation,
        "page_number": snippet.citation.page_number,
        "snippet_text": snippet.snippet_text,
    }


def _write_jsonl(
    path: Path, rows: tuple[RetrievalArtifactRow, ...], boundary: _WriteBoundary
) -> None:
    payload = "" if not rows else "\n".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows
    ) + "\n"
    _write_text_atomic(path, payload, boundary)


def _write_text_atomic(path: Path, payload: str, boundary: _WriteBoundary) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        _guard_write_path(path, boundary)
        _guard_write_path(temporary, boundary)
        _ = temporary.write_text(payload, encoding="utf-8")
        _ = temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _guard_write_directory(path: Path, boundary: _WriteBoundary) -> None:
    reason = "startup RAG artifact directory escapes or uses symlinks"
    _ = ensure_no_symlink_path_components(path, reason)
    _ = ensure_source_document_is_not_write_target(boundary.source_root, path)


def _guard_write_path(path: Path, boundary: _WriteBoundary) -> None:
    reason = "startup RAG artifact path escapes or uses symlinks"
    _ = ensure_contained_write_path(boundary.root, path, reason)
    _ = ensure_no_symlink_leaf(path, reason)
    _ = ensure_source_document_is_not_write_target(boundary.source_root, path)


def _optional_qwen_bridge_path(rag_path: Path) -> Path | None:
    path = qwen_bridge_results_path(rag_path)
    return path if path.is_dir() else None


def _startup_embedder(model_cache_root: Path) -> TextEmbedder:
    from modules.rag.retrieval.embedding_backend import (  # noqa: PLC0415
        LocalTransformerTextEmbedder,
    )

    return LocalTransformerTextEmbedder.from_model_cache(model_cache_root)
