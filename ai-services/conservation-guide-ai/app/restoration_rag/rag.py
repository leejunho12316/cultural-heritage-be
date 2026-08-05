"""복원(restoration) 노드 RAG 검색 모듈.

app/restoration_rag/index/ 에 build_index.py로 생성된 TF-IDF 인덱스를 로드하여
질의에 대한 근거 문헌 청크를 검색한다.

app/nodes/restoration.py 의 _retrieve_restoration_context()가 이 함수를
try/except로 감싸므로, 여기서 발생하는 예외(인덱스 없음 포함)는 항상
"근거 부족" 상태로 안전하게 폴백된다 — 노드가 죽는 일은 없다.
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

from sklearn.metrics.pairwise import cosine_similarity

BASE_DIR = Path(__file__).resolve().parent
INDEX_DIR = BASE_DIR / "index"

ALLOWED_STAGES = {"restoration"}


class IndexNotAvailableError(RuntimeError):
    """인덱스 파일이 없거나 비어 있어 검색할 수 없을 때 발생."""


_CACHE: dict[str, Any] = {}


def _load_index() -> tuple[Any, Any, list[dict[str, Any]]]:
    if _CACHE:
        return _CACHE["vectorizer"], _CACHE["matrix"], _CACHE["chunks"]

    vectorizer_path = INDEX_DIR / "vectorizer.pkl"
    matrix_path = INDEX_DIR / "matrix.pkl"
    chunks_path = INDEX_DIR / "chunks.json"

    if not (vectorizer_path.exists() and matrix_path.exists() and chunks_path.exists()):
        raise IndexNotAvailableError(
            "restoration_rag 인덱스가 존재하지 않습니다. "
            "python -m app.restoration_rag.build_index 를 먼저 실행하세요."
        )

    with vectorizer_path.open("rb") as f:
        vectorizer = pickle.load(f)
    with matrix_path.open("rb") as f:
        matrix = pickle.load(f)
    with chunks_path.open("r", encoding="utf-8") as f:
        chunks = json.load(f)

    if matrix.shape[0] == 0 or not chunks:
        raise IndexNotAvailableError("restoration_rag 인덱스가 비어 있습니다.")

    _CACHE["vectorizer"] = vectorizer
    _CACHE["matrix"] = matrix
    _CACHE["chunks"] = chunks
    return vectorizer, matrix, chunks


def retrieve_reference_context(
    query: str,
    *,
    filters: dict[str, Any] | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """질의에 대한 근거 문헌 청크를 top_k개 반환한다.

    filters 예시: {"material": ["ceramic", "general"], "process_stage": "restoration"}

    Returns:
        [{"content", "source", "filename", "page", "material", "process_stage", "score"}, ...]
    """
    filters = filters or {}

    process_stage = filters.get("process_stage", "restoration")
    if process_stage not in ALLOWED_STAGES:
        raise ValueError(
            f"restoration_rag.rag는 process_stage={ALLOWED_STAGES}만 허용합니다 "
            f"(받은 값: {process_stage!r})"
        )

    if not query or not query.strip():
        return []

    vectorizer, matrix, chunks = _load_index()

    materials = filters.get("material")
    if materials:
        materials = set(materials)
        candidate_indices = [i for i, c in enumerate(chunks) if c["material"] in materials]
    else:
        candidate_indices = list(range(len(chunks)))

    if not candidate_indices:
        return []

    query_vec = vectorizer.transform([query])
    candidate_matrix = matrix[candidate_indices]
    scores = cosine_similarity(query_vec, candidate_matrix)[0]

    ranked = sorted(zip(candidate_indices, scores), key=lambda x: x[1], reverse=True)
    top = [(idx, score) for idx, score in ranked if score > 0][:top_k]

    results = []
    for idx, score in top:
        chunk = chunks[idx]
        results.append(
            {
                "content": chunk["content"],
                "source": chunk["source"],
                "filename": chunk["filename"],
                "page": chunk["page"],
                "material": chunk["material"],
                "process_stage": chunk["process_stage"],
                "score": round(float(score), 4),
            }
        )
    return results
