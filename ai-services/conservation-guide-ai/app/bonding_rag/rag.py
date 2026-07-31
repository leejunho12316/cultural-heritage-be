"""재질/처리단계 강제 필터를 적용하는 접합 문헌 검색기."""

from __future__ import annotations

import json
import math
import threading
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer


PACKAGE_DIR = Path(__file__).resolve().parent
CHUNKS_PATH = PACKAGE_DIR / "index" / "chunks.jsonl"
ALLOWED_MATERIALS = {
    "ceramic",
    "metal",
    "wood",
    "stone",
    "paper",
    "textile",
    "general",
}
ALLOWED_STAGES = {"bonding"}

_LOCK = threading.Lock()
_INDEX_MTIME_NS: int | None = None
_CHUNKS: list[dict[str, Any]] = []
_VECTORIZER: TfidfVectorizer | None = None
_MATRIX: Any = None


def _validate_filters(filters: dict[str, Any] | None) -> tuple[set[str], str]:
    """무필터/부정확한 호출을 거부해 안전하지 않은 전체 검색을 막는다."""
    if not isinstance(filters, dict):
        raise ValueError("filters는 필수입니다.")

    raw_materials = filters.get("material")
    if isinstance(raw_materials, str):
        raw_materials = [raw_materials]
    if not isinstance(raw_materials, (list, tuple, set)) or not raw_materials:
        raise ValueError("filters.material에는 하나 이상의 표준 재질이 필요합니다.")

    materials = {
        str(material).strip().lower()
        for material in raw_materials
        if str(material).strip()
    }
    unknown_materials = materials - ALLOWED_MATERIALS
    if unknown_materials:
        raise ValueError(
            "지원하지 않는 material 필터: " + ", ".join(sorted(unknown_materials))
        )

    stage = str(filters.get("process_stage", "")).strip().lower()
    if stage not in ALLOWED_STAGES:
        raise ValueError("filters.process_stage는 'bonding'이어야 합니다.")
    return materials, stage


def _load_index() -> tuple[
    list[dict[str, Any]],
    TfidfVectorizer,
    Any,
]:
    global _INDEX_MTIME_NS, _CHUNKS, _VECTORIZER, _MATRIX

    if not CHUNKS_PATH.is_file():
        raise FileNotFoundError(
            f"접합 RAG 인덱스가 없습니다: {CHUNKS_PATH}. "
            "프로젝트 루트에서 `python -m app.bonding_rag.build_index`를 실행하세요."
        )

    mtime_ns = CHUNKS_PATH.stat().st_mtime_ns
    if (
        _INDEX_MTIME_NS == mtime_ns
        and _VECTORIZER is not None
        and _MATRIX is not None
    ):
        return _CHUNKS, _VECTORIZER, _MATRIX

    with _LOCK:
        mtime_ns = CHUNKS_PATH.stat().st_mtime_ns
        if (
            _INDEX_MTIME_NS == mtime_ns
            and _VECTORIZER is not None
            and _MATRIX is not None
        ):
            return _CHUNKS, _VECTORIZER, _MATRIX

        chunks: list[dict[str, Any]] = []
        with CHUNKS_PATH.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                if not line.strip():
                    continue
                chunk = json.loads(line)
                required = {
                    "id",
                    "source",
                    "page",
                    "material",
                    "process_stage",
                    "content",
                }
                if not required.issubset(chunk):
                    raise ValueError(
                        f"인덱스 {line_number}행에 필수 필드가 없습니다."
                    )
                if chunk["material"] not in ALLOWED_MATERIALS:
                    raise ValueError(
                        f"인덱스 {line_number}행 material이 잘못되었습니다."
                    )
                if chunk["process_stage"] not in ALLOWED_STAGES:
                    raise ValueError(
                        f"인덱스 {line_number}행 process_stage가 잘못되었습니다."
                    )
                chunks.append(chunk)

        if not chunks:
            raise ValueError("접합 RAG 인덱스가 비어 있습니다.")

        # 한글 띄어쓰기/OCR 오차에 비교적 견고한 문자 n-gram 기반 검색이다.
        vectorizer = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(2, 5),
            min_df=1,
            sublinear_tf=True,
            norm="l2",
        )
        matrix = vectorizer.fit_transform(
            f"{chunk['material']} {chunk['content']}" for chunk in chunks
        )
        _CHUNKS = chunks
        _VECTORIZER = vectorizer
        _MATRIX = matrix
        _INDEX_MTIME_NS = mtime_ns
        return _CHUNKS, _VECTORIZER, _MATRIX


def retrieve_reference_context(
    query: str,
    *,
    filters: dict[str, Any],
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """검증된 재질/접합 청크 안에서만 관련 문헌을 반환한다.

    반환 필드:
        source, page, material, process_stage, content, score
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("검색 query는 비어 있을 수 없습니다.")
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 20:
        raise ValueError("top_k는 1~20 범위의 정수여야 합니다.")

    materials, stage = _validate_filters(filters)
    chunks, vectorizer, matrix = _load_index()

    candidate_indices = [
        index
        for index, chunk in enumerate(chunks)
        if chunk["material"] in materials
        and chunk["process_stage"] == stage
    ]
    if not candidate_indices:
        return []

    query_vector = vectorizer.transform([query[:8000]])
    raw_scores = np.asarray(
        matrix[candidate_indices].dot(query_vector.T).toarray()
    ).reshape(-1)

    ranked = sorted(
        zip(candidate_indices, raw_scores, strict=True),
        key=lambda item: (-float(item[1]), chunks[item[0]]["id"]),
    )

    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, score in ranked:
        if len(results) >= top_k:
            break
        if not math.isfinite(float(score)) or float(score) <= 0:
            continue

        chunk = chunks[index]
        # 반환 직전에도 동일한 강제 필터를 재검사한다.
        if (
            chunk["material"] not in materials
            or chunk["process_stage"] != stage
        ):
            continue
        dedupe_key = (
            str(chunk["source"]),
            str(chunk["page"]),
            str(chunk["content"]),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        results.append({
            "source": chunk["source"],
            "page": chunk["page"],
            "material": chunk["material"],
            "process_stage": chunk["process_stage"],
            "content": chunk["content"],
            "score": round(float(score), 6),
        })
    return results
