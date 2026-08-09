"""Project-level startup runner for candidate RAG sidecar generation."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from modules.rag.corpus.corpus import Corpus
from modules.rag.corpus.document_index import (
    build_document_index,
)
from modules.rag.operations.candidate_card_terms import qwen_query_signature
from modules.rag.operations.candidate_sidecar_artifacts import (
    PromptQueryRecord,
    read_rough_records,
)
from modules.rag.operations.candidate_sidecars import (
    CandidateRagSidecarInputs,
    build_candidate_rag_sidecars,
    write_candidate_rag_sidecars,
)
from modules.rag.qwen.qwen_bridge_artifacts import (
    qwen_bridge_results_path,
    read_qwen_bridge_results,
)
from modules.rag.retrieval.concept_translations import korean_terms_for_token
from modules.rag.retrieval.terms import (
    ObservedTermSource,
    observed_korean_term,
    query_terms,
)
from modules.rag.retrieval.vector_index import (
    TextEmbedder,
    build_vector_index,
    vector_retrieve,
)
from modules.rag.retrieval.vector_store import (
    read_vector_index_artifacts,
    write_vector_index_artifacts,
)
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
    exclusive_file_lock,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from modules.orchestration.stage_paths import StagePathMap
    from modules.rag.corpus.document_index import DocumentChunk
    from modules.rag.operations.candidate_sidecar_artifacts import RoughRagCandidate
    from modules.rag.retrieval.retrieval import RetrievalSnippet
    from modules.rag.retrieval.vector_index import VectorIndex
    from modules.shared import CandidateId, QwenBridgeResult

type RetrievalArtifactRow = dict[str, str | int | float | list[str] | None]


class _RagStageRequest(Protocol):
    @property
    def paths(self) -> StagePathMap: ...

    @property
    def dry_run(self) -> bool: ...

    @property
    def model_cache_root(self) -> Path: ...

    @property
    def device(self) -> str: ...


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


# RAG 스테이지의 오케스트레이션 진입점. 파이프라인 러너가 각 스테이지를 순회하며
# 호출하며, dry_run이면 아무 산출물도 만들지 않고 즉시 OK를 반환한다. 계약/경로
# 안전성 예외는 여기서 잡아 표준 ExitCode로 변환해 상위 오케스트레이션에 넘긴다.
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
    except (ContractValidationError, OSError, PathSafetyError) as error:
        print(  # noqa: T201
            f"rag: failed: {type(error).__name__}: {error}", file=sys.stderr
        )
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


# run_rag_stage에서 호출되어 queries.jsonl / prompt_rag_results.jsonl을 새로
# 만든다. rough masking 후보에서 쿼리를 뽑아 벡터 인덱스로 검색한 뒤 결과를
# 그대로 덮어쓰므로, 이전 실행에서 남아 있던 산출물은 매번 재생성된다.
def _materialize_missing_retrieval_artifacts(request: _RagStageRequest) -> None:
    queries_path = request.paths.rag / "queries.jsonl"
    results_path = request.paths.rag / "prompt_rag_results.jsonl"
    rough_records = read_rough_records(request.paths.rough_masking)
    qwen_results = _resolved_qwen_results(request.paths.rag)
    queries = _prompt_queries(rough_records, qwen_results)
    embedder = _startup_embedder(request.model_cache_root, request.device)
    vector_index = _locked_corpus_vector_index(request.model_cache_root, embedder)
    result_rows: list[RetrievalArtifactRow] = []
    for query in queries:
        snippets = vector_retrieve(
            vector_index,
            _query_text_for_retrieval(query),
            embedder,
            top_k=TOP_K_PER_PROMPT,
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


# rag_run_directory 아래에 qwen_bridge_results 디렉터리가 있으면 후보별 Qwen
# 결과를 읽어온다(visual_cue_generation이 rag 스테이지보다 먼저 실행되며
# 이 디렉터리에 결과를 써둔다). 없으면(스킵 모드 등) 빈 매핑을 돌려줘 이후
# 모든 후보가 qwen_signature=()로 오늘과 동일하게 동작하도록 한다.
# _materialize_missing_retrieval_artifacts가 쿼리 생성 전에 호출한다.
def _resolved_qwen_results(
    rag_run_directory: Path,
) -> Mapping[CandidateId, QwenBridgeResult]:
    path = _optional_qwen_bridge_path(rag_run_directory)
    if path is None:
        return {}
    return read_qwen_bridge_results(path)


# rough masking 후보 목록으로부터 검색에 사용할 쿼리 레코드를 생성한다.
# _materialize_missing_retrieval_artifacts에서 호출되며, 중복 제거 방식에 대한
# 주의사항은 아래 주석을 참고.
def _prompt_queries(
    rough_records: tuple[RoughRagCandidate, ...],
    qwen_results: Mapping[CandidateId, QwenBridgeResult],
) -> tuple[PromptQueryRecord, ...]:
    # (lane, prompt_text, qwen_signature) 조합 기준으로 유일한 것만 남기고
    # 중복을 제거한다. qwen_signature는 candidate_card_terms.qwen_query_signature가
    # 후보 고유 Qwen 서술어(selected_terms/extracted_descriptors)로 계산하며,
    # Qwen 데이터가 없는 후보는 빈 튜플이 되어 예전처럼 (lane, prompt_text)만으로
    # 묶인다. 같은 시드 프롬프트를 쓰더라도 Qwen 서술어가 다른 후보는 이제
    # 서로 다른 쿼리로 조인되어(candidate_sidecar_artifacts.joined_query) 각자
    # 독립적인 검색 결과/인용을 받는다.
    queries: list[PromptQueryRecord] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for rough in rough_records:
        qwen_signature = qwen_query_signature(rough.candidate_id, qwen_results)
        key = (rough.lane, rough.prompt_text, qwen_signature)
        if key in seen:
            continue
        seen.add(key)
        queries.append(
            PromptQueryRecord(
                lane=rough.lane,
                prompt_text=rough.prompt_text,
                query_id=f"{rough.lane}:prompt-{len(queries) + 1:04d}",
                qwen_signature=qwen_signature,
            )
        )
    return tuple(queries)


# _materialize_missing_retrieval_artifacts가 벡터 검색에 넘길 쿼리 문자열을
# 만들 때 호출한다. 배경/주의사항은 아래 docstring 참고.
def _bilingual_query_text(prompt_text: str) -> str:
    """Widen a seed-prompt query with allowlisted Korean equivalents.

    The corpus is largely Korean-language literature; an English-only query
    against it under-retrieves. Korean terms come only from the fixed
    mapping-table translation of the existing allowlisted vocabulary (never
    invented per-candidate) and are used solely to build this search string,
    never surfaced as observed evidence or report text.
    """
    tokens = _query_tokens(prompt_text)
    observed = tuple(
        observed_korean_term(korean_term, ObservedTermSource.MAPPING_TABLE)
        for token in tokens
        for korean_term in korean_terms_for_token(token)
    )
    terms = query_terms(observed, tokens)
    return " ".join(terms.lexical_tokens)


# 시드 프롬프트의 이중언어 검색 문자열에 후보 고유 Qwen 서술어를 이어붙인다.
# qwen_signature가 비어 있으면(Qwen 데이터 없음/실패) _bilingual_query_text와
# 완전히 동일한 문자열을 돌려준다. _materialize_missing_retrieval_artifacts가
# vector_retrieve에 넘길 질의 문자열을 만들 때 호출한다.
def _query_text_for_retrieval(query: PromptQueryRecord) -> str:
    base = _bilingual_query_text(query.prompt_text)
    if not query.qwen_signature:
        return base
    return f"{base} {' '.join(query.qwen_signature)}"


# 프롬프트 텍스트를 검색용 토큰으로 정규화한다. _bilingual_query_text와
# _evidence_terms가 공유해서 쓰는 토크나이즈 로직이다.
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


# 쿼리 토큰에 PROMPT_CUE_TERMS로 정의된 동의어를 덧붙여 반환한다.
# _materialize_missing_retrieval_artifacts에서 결과 행의 matched_terms를
# 구성할 때 호출된다.
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
        "qwen_signature": list(query.qwen_signature),
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


# 쿼리/결과 행들을 JSONL로 직렬화해 원자적으로 기록한다.
# _materialize_missing_retrieval_artifacts가 두 산출물 파일을 쓸 때 호출한다.
def _write_jsonl(
    path: Path, rows: tuple[RetrievalArtifactRow, ...], boundary: _WriteBoundary
) -> None:
    payload = "" if not rows else "\n".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows
    ) + "\n"
    _write_text_atomic(path, payload, boundary)


# 임시 파일에 쓴 뒤 rename으로 교체하는 원자적 쓰기 패턴이다.
# 쓰기 도중 실패하면 임시 파일을 정리하고 예외를 다시 던진다.
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


# 심볼릭 링크·경로 이탈로부터 RAG 산출물 디렉터리를 보호한다. 실제 쓰기 전에
# 항상 먼저 호출되는 안전장치이며, 파일 단위 검사는 아래 _guard_write_path가 맡는다.
def _guard_write_directory(path: Path, boundary: _WriteBoundary) -> None:
    reason = "startup RAG artifact directory escapes or uses symlinks"
    _ = ensure_no_symlink_path_components(path, reason)
    _ = ensure_source_document_is_not_write_target(boundary.source_root, path)


# _guard_write_directory와 짝을 이루는 파일 단위 안전장치. _write_text_atomic이
# 실제 파일을 쓰기 전에 대상 경로와 임시 경로 모두에 대해 호출한다.
def _guard_write_path(path: Path, boundary: _WriteBoundary) -> None:
    reason = "startup RAG artifact path escapes or uses symlinks"
    _ = ensure_contained_write_path(boundary.root, path, reason)
    _ = ensure_no_symlink_leaf(path, reason)
    _ = ensure_source_document_is_not_write_target(boundary.source_root, path)


def _optional_qwen_bridge_path(rag_path: Path) -> Path | None:
    path = qwen_bridge_results_path(rag_path)
    return path if path.is_dir() else None


# 로컬 모델 캐시에서 텍스트 임베더를 로드한다.
# _materialize_missing_retrieval_artifacts가 벡터 검색 전에 호출한다.
def _startup_embedder(model_cache_root: Path, device: str) -> TextEmbedder:
    from modules.rag.retrieval.embedding_backend import (  # noqa: PLC0415
        LocalTransformerTextEmbedder,
    )

    return LocalTransformerTextEmbedder.from_model_cache(model_cache_root, device)


# _materialize_missing_retrieval_artifacts에서 호출되어 임베딩 모델에 맞는 코퍼스
# 벡터 인덱스를 준비한다. 락과 관련된 주의사항은 아래 docstring 참고.
def _locked_corpus_vector_index(
    model_cache_root: Path, embedder: TextEmbedder
) -> VectorIndex:
    """Rebuild/reuse the shared corpus vector index under a cross-process lock.

    Concurrent runs against the same model cache root would otherwise race
    on the same corpus-cache and vector-index files under
    `model_cache_root/rag/`.
    """
    lock_path = model_cache_root / "rag" / ".materialize.lock"
    with exclusive_file_lock(model_cache_root, lock_path):
        rows = startup_corpus_rows(model_cache_root)
        index = build_document_index(Corpus.from_metadata(rows), MAX_SNIPPET_CHARS)
        if not index.chunks:
            field = "document_corpus"
            reason = "deterministic local corpus/index unavailable"
            raise ContractValidationError(field, reason)
        return _load_or_build_vector_index(model_cache_root, index.chunks, embedder)


# 캐시된 벡터 인덱스가 모델/청크 구성과 정확히 일치하면 그대로 재사용하고,
# 아니면 새로 임베딩을 계산해 다시 저장한다. _locked_corpus_vector_index가
# 락을 잡은 상태에서 호출한다.
def _load_or_build_vector_index(
    model_cache_root: Path,
    chunks: tuple[DocumentChunk, ...],
    embedder: TextEmbedder,
) -> VectorIndex:
    cached = read_vector_index_artifacts(
        model_cache_root,
        expected_model_id=embedder.model_id,
        expected_chunks=chunks,
    )
    if cached is not None:
        return cached
    vector_index = build_vector_index(chunks, embedder)
    write_vector_index_artifacts(model_cache_root, vector_index)
    return vector_index
