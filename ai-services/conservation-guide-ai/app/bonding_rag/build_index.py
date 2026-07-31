"""PDF 8권에서 접합 단계 전용 검색 인덱스를 생성한다.

실행:
    python -m app.bonding_rag.build_index
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from pypdf import PdfReader


LOGGER = logging.getLogger(__name__)
PACKAGE_DIR = Path(__file__).resolve().parent
DOCUMENTS_DIR = PACKAGE_DIR / "documents"
INDEX_DIR = PACKAGE_DIR / "index"
MANIFEST_PATH = PACKAGE_DIR / "manifest.json"
CHUNKS_PATH = INDEX_DIR / "chunks.jsonl"
INDEX_METADATA_PATH = INDEX_DIR / "metadata.json"

ALLOWED_MATERIALS = {
    "ceramic",
    "metal",
    "wood",
    "stone",
    "paper",
    "textile",
    "general",
}

# 접합(접착제 선택·접합 방법)에 직접 관계된 청크만 인덱싱한다.
BONDING_TERMS = (
    "접합",
    "접착제",
    "접착력",
    "접착강도",
    "접착",
    "본드",
    "부착",
    "가역성",
    "재처리",
    "경화",
    "접합면",
    "파단면",
    "임시접합",
    "예비접합",
    "복합접합",
    "단일접합",
    "결합접합",
    "모세관접합",
    "adhesive",
    "adhesion",
    "bonding",
    "bond",
    "reversibility",
    "cyanoacrylate",
    "epoxy",
    "paraloid",
)


def _load_manifest() -> dict[str, Any]:
    with MANIFEST_PATH.open("r", encoding="utf-8") as file:
        manifest = json.load(file)
    if manifest.get("schema_version") != 1:
        raise ValueError("지원하지 않는 manifest schema_version입니다.")
    return manifest


def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\x00", " ")
    text = re.sub(r"[​-‏‪-‮﻿]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _material_for_page(document: dict[str, Any], page: int) -> str | None:
    default_material = document.get("default_material")
    if default_material:
        if default_material not in ALLOWED_MATERIALS:
            raise ValueError(f"허용되지 않은 material: {default_material}")
        return default_material

    for page_range in document.get("material_page_ranges", []):
        if int(page_range["start"]) <= page <= int(page_range["end"]):
            material = str(page_range["material"])
            if material not in ALLOWED_MATERIALS:
                raise ValueError(f"허용되지 않은 material: {material}")
            return material
    return None


def _is_bonding_chunk(text: str) -> bool:
    lowered = text.casefold()
    return any(term.casefold() in lowered for term in BONDING_TERMS)


def _split_text(
    text: str,
    *,
    chunk_size: int = 1200,
    overlap: int = 180,
) -> Iterable[str]:
    """문단 경계를 우선 사용하고 긴 문단만 고정 길이로 분할한다."""
    if chunk_size <= overlap:
        raise ValueError("chunk_size는 overlap보다 커야 합니다.")

    paragraphs = [
        re.sub(r"\s+", " ", paragraph).strip()
        for paragraph in re.split(r"\n\s*\n", text)
        if paragraph.strip()
    ]
    if not paragraphs:
        paragraphs = [re.sub(r"\s+", " ", text).strip()]

    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > chunk_size:
            if current:
                chunks.append(current)
                current = ""
            step = chunk_size - overlap
            for start in range(0, len(paragraph), step):
                part = paragraph[start : start + chunk_size].strip()
                if part:
                    chunks.append(part)
                if start + chunk_size >= len(paragraph):
                    break
            continue

        candidate = f"{current}\n{paragraph}".strip()
        if current and len(candidate) > chunk_size:
            chunks.append(current)
            tail = current[-overlap:].strip()
            current = f"{tail}\n{paragraph}".strip()
        else:
            current = candidate

    if current:
        chunks.append(current)
    yield from chunks


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# 텍스트 레이어가 없는 스캔 페이지의 대체 경로.
# pypdf의 임베드 이미지 추출은 스캔이 여러 조각으로 나뉘어 저장된 경우 페이지 일부만 가져오는
# 문제가 있어, PyMuPDF로 페이지 전체를 하나의 이미지로 렌더링한 뒤 VLM에 전달한다.
# 렌더링/VLM 호출이 실패하면 빈 문자열을 반환하고, 호출부(_extract_document)가 평소처럼
# skipped_pages로 처리한다.
def _ocr_page_via_vlm(pdf_path: Path, filename: str, page_number: int) -> str:
    import fitz  # PyMuPDF. 지연 임포트: OCR이 필요 없는 문서에서는 로드하지 않는다.

    try:
        with fitz.open(pdf_path) as doc:
            pixmap = doc[page_number - 1].get_pixmap(dpi=200)
            png_bytes = pixmap.tobytes("png")
    except Exception as exc:
        LOGGER.warning("%s p.%d: 페이지 렌더링 실패 (%s)", filename, page_number, exc)
        return ""

    encoded = base64.b64encode(png_bytes).decode("utf-8")

    from ..llm import vision_llm  # 지연 임포트: OCR이 필요 없는 문서에서는 vision 모델을 로드하지 않는다.
    from langchain_core.messages import HumanMessage

    prompt = """이 이미지는 보존과학 문헌의 스캔된 한 페이지입니다.
    이미지 안의 본문 텍스트를 그대로(의역·요약 없이) 옮겨 적어주세요.
    표/각주/참고문헌은 알아볼 수 있는 범위에서만 포함하고, 읽을 수 없는 부분은 생략하세요."""

    try:
        response = vision_llm.invoke([
            HumanMessage(content=[
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
            ])
        ])
        LOGGER.info("%s p.%d: VLM OCR로 텍스트 %d자 확보", filename, page_number, len(response.content or ""))
        return response.content or ""
    except Exception as exc:
        LOGGER.warning("%s p.%d: VLM OCR 실패 (%s)", filename, page_number, exc)
        return ""


def _extract_document(
    document: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    filename = str(document["filename"])
    path = DOCUMENTS_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(
            f"필수 PDF가 없습니다: {path}. documents/에 정확한 파일명으로 넣으세요."
        )

    reader = PdfReader(path)
    chunks: list[dict[str, Any]] = []
    skipped_pages = 0
    extraction_errors: list[dict[str, Any]] = []

    for page_number, pdf_page in enumerate(reader.pages, start=1):
        material = _material_for_page(document, page_number)
        if material is None:
            # 종합 문서에서 재질 범위를 확정하지 못한 페이지는 안전하게 제외한다.
            skipped_pages += 1
            continue

        try:
            page_text = _normalize_text(pdf_page.extract_text() or "")
        except Exception as exc:  # 한 페이지 실패가 전체 인덱싱을 막지 않게 기록한다.
            extraction_errors.append({
                "page": page_number,
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        # 텍스트 레이어가 없는 스캔 페이지: 페이지 전체를 렌더링해서 VLM으로 읽어본다.
        if not page_text:
            page_text = _normalize_text(_ocr_page_via_vlm(path, filename, page_number))

        if not page_text:
            skipped_pages += 1
            continue

        for chunk_number, content in enumerate(_split_text(page_text), start=1):
            if not _is_bonding_chunk(content):
                continue
            chunk_id = hashlib.sha256(
                f"{filename}:{page_number}:{chunk_number}:{content}".encode("utf-8")
            ).hexdigest()[:24]
            chunks.append({
                "id": chunk_id,
                "source": str(document["source"]),
                "filename": filename,
                "page": page_number,
                "material": material,
                "process_stage": "bonding",
                "content": content,
            })

    report = {
        "filename": filename,
        "source": document["source"],
        "pages": len(reader.pages),
        "encrypted": bool(reader.is_encrypted),
        "sha256": _sha256(path),
        "indexed_chunks": len(chunks),
        "skipped_pages": skipped_pages,
        "extraction_errors": extraction_errors,
    }
    return chunks, report


def build_index() -> dict[str, Any]:
    manifest = _load_manifest()
    expected = {str(item["filename"]) for item in manifest["documents"]}
    actual = {path.name for path in DOCUMENTS_DIR.glob("*.pdf")}
    missing = sorted(expected - actual)
    if missing:
        raise FileNotFoundError(
            "documents/에 필수 PDF가 없습니다: " + ", ".join(missing)
        )

    all_chunks: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    for document in manifest["documents"]:
        chunks, report = _extract_document(document)
        all_chunks.extend(chunks)
        reports.append(report)
        LOGGER.info(
            "%s: %d개 청크 생성",
            report["filename"],
            report["indexed_chunks"],
        )

    if not all_chunks:
        raise RuntimeError("접합 문헌 청크가 생성되지 않았습니다.")

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    temp_chunks_path = CHUNKS_PATH.with_suffix(".jsonl.tmp")
    with temp_chunks_path.open("w", encoding="utf-8") as file:
        for chunk in all_chunks:
            file.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    temp_chunks_path.replace(CHUNKS_PATH)

    counts = Counter(chunk["material"] for chunk in all_chunks)
    metadata = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "chunk_count": len(all_chunks),
        "material_chunk_counts": dict(sorted(counts.items())),
        "documents": reports,
    }
    temp_metadata_path = INDEX_METADATA_PATH.with_suffix(".json.tmp")
    with temp_metadata_path.open("w", encoding="utf-8") as file:
        json.dump(metadata, file, ensure_ascii=False, indent=2)
        file.write("\n")
    temp_metadata_path.replace(INDEX_METADATA_PATH)
    return metadata


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )
    metadata = build_index()
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
