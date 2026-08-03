"""복원(restoration) 노드 RAG 인덱스 빌더.

manifest.json에 등록된 PDF 문헌을 읽어 텍스트를 청크 단위로 추출하고,
TF-IDF(char n-gram) 인덱스를 생성하여 app/restoration_rag/index/ 에 저장한다.

manifest.json 스키마 (schema_version: 1):
{
  "schema_version": 1,
  "documents": [
    {"filename": str, "source": str, "default_material": str},
    {"filename": str, "source": str,
     "material_page_ranges": [{"start": int, "end": int, "material": str}, ...]}
  ]
}

실행:
    python -m app.restoration_rag.build_index
"""
from __future__ import annotations

import json
import pickle
import re
import sys
from pathlib import Path
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    from PyPDF2 import PdfReader  # type: ignore

BASE_DIR = Path(__file__).resolve().parent
DOCUMENTS_DIR = BASE_DIR / "documents"
INDEX_DIR = BASE_DIR / "index"
MANIFEST_PATH = BASE_DIR / "manifest.json"

PROCESS_STAGE = "restoration"

# 복원(결손부 충전/합성수지) 관련 키워드. 이 키워드가 하나도 없는 청크는
# 노이즈로 판단하여 인덱스에서 제외한다.
RESTORATION_TERMS = [
    "복원", "복원제", "복원재", "복원재료", "충전", "충전재", "충진재",
    "메움", "메움제", "결손", "결손부", "결실", "보철",
    "합성수지", "에폭시", "에폭시수지", "에폭시퍼티", "퍼티",
    "색맞춤", "가역성", "형틀", "틈메움", "성형법", "형틀복원",
    "restoration", "filling", "infill", "gap-filling", "gap filling",
    "retouching", "epoxy", "putty",
]

CHUNK_MAX_CHARS = 900
CHUNK_OVERLAP = 150


def _load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"manifest.json을 찾을 수 없습니다: {MANIFEST_PATH}")
    with MANIFEST_PATH.open("r", encoding="utf-8") as f:
        manifest = json.load(f)
    if manifest.get("schema_version") != 1:
        raise ValueError(
            f"지원하지 않는 manifest schema_version: {manifest.get('schema_version')!r} (1이어야 함)"
        )
    return manifest


def _resolve_material_for_page(doc_entry: dict[str, Any], page_number: int) -> str | None:
    """1-indexed PDF page_number에 대응하는 재질을 반환한다.

    material_page_ranges가 있는 문헌은 범위 밖 페이지를 None으로 반환해
    인덱싱 대상에서 제외한다. material_page_ranges가 없으면 default_material을 쓴다.
    """
    page_ranges = doc_entry.get("material_page_ranges")
    if page_ranges:
        for rng in page_ranges:
            if rng["start"] <= page_number <= rng["end"]:
                return rng["material"]
        return None
    return doc_entry.get("default_material", "general")


def _split_into_chunks(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_MAX_CHARS, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = end - CHUNK_OVERLAP
    return chunks


def _contains_restoration_term(text: str) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in RESTORATION_TERMS)


def build_index() -> dict[str, Any]:
    manifest = _load_manifest()
    documents = manifest.get("documents", [])

    all_chunks: list[dict[str, Any]] = []
    material_chunk_counts: dict[str, int] = {}
    skipped_pages: list[dict[str, Any]] = []
    extraction_errors: list[dict[str, Any]] = []

    for doc_entry in documents:
        filename = doc_entry["filename"]
        source = doc_entry.get("source", filename)
        pdf_path = DOCUMENTS_DIR / filename

        if not pdf_path.exists():
            extraction_errors.append(
                {"filename": filename, "error": "파일 없음 (documents/ 폴더 확인 필요)"}
            )
            continue

        try:
            reader = PdfReader(str(pdf_path))
        except Exception as exc:  # noqa: BLE001
            extraction_errors.append({"filename": filename, "error": f"PDF 열기 실패: {exc}"})
            continue

        for page_index, page in enumerate(reader.pages):
            page_number = page_index + 1  # 1-indexed
            material = _resolve_material_for_page(doc_entry, page_number)
            if material is None:
                continue  # material_page_ranges 밖의 페이지는 스킵

            try:
                page_text = page.extract_text() or ""
            except Exception as exc:  # noqa: BLE001
                extraction_errors.append(
                    {"filename": filename, "page": page_number, "error": f"텍스트 추출 실패: {exc}"}
                )
                continue

            if not page_text.strip():
                skipped_pages.append(
                    {"filename": filename, "page": page_number, "reason": "빈 페이지"}
                )
                continue

            for chunk_text in _split_into_chunks(page_text):
                if not _contains_restoration_term(chunk_text):
                    continue
                all_chunks.append(
                    {
                        "chunk_id": f"{filename}:{page_number}:{len(all_chunks)}",
                        "content": chunk_text,
                        "filename": filename,
                        "source": source,
                        "page": page_number,
                        "material": material,
                        "process_stage": PROCESS_STAGE,
                    }
                )
                material_chunk_counts[material] = material_chunk_counts.get(material, 0) + 1

    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    summary = {
        "chunk_count": len(all_chunks),
        "material_chunk_counts": material_chunk_counts,
        "skipped_pages": skipped_pages,
        "extraction_errors": extraction_errors,
    }

    if not all_chunks:
        # 청크가 하나도 없으면 벡터라이저를 만들 수 없다.
        # 기존 인덱스 파일을 정리하여 rag.py가 "인덱스 없음"으로 안전하게
        # 처리하도록 한다 (restoration_material_node가 죽지 않게 하기 위함).
        for fname in ("vectorizer.pkl", "matrix.pkl", "chunks.json"):
            fpath = INDEX_DIR / fname
            if fpath.exists():
                fpath.unlink()
        with (INDEX_DIR / "build_summary.json").open("w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return summary

    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1)
    matrix = vectorizer.fit_transform([c["content"] for c in all_chunks])

    with (INDEX_DIR / "vectorizer.pkl").open("wb") as f:
        pickle.dump(vectorizer, f)
    with (INDEX_DIR / "matrix.pkl").open("wb") as f:
        pickle.dump(matrix, f)
    with (INDEX_DIR / "chunks.json").open("w", encoding="utf-8") as f:
        json.dump(all_chunks, f, ensure_ascii=False, indent=2)
    with (INDEX_DIR / "build_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    try:
        build_index()
    except Exception as exc:  # noqa: BLE001
        print(f"인덱스 빌드 실패: {exc}", file=sys.stderr)
        sys.exit(1)
