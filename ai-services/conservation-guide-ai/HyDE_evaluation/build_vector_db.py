"""평가 전용 Chroma Vector DB 구축 스크립트.

eval_original_pdfs/ 의 PDF를 읽어 eval_vector_store/ 에 저장.
프로덕션 reinforcement_rag/vector_store/ 와 완전히 분리된 별도 DB.

사용법 (conservation-guide-ai/ 디렉터리에서):
    python -m HyDE_evaluation.build_vector_db
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
load_dotenv()

from langchain_chroma import Chroma
from langchain_community.document_loaders import PyPDFLoader
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .config import (
    PDFS_DIR,
    VECTOR_STORE_DIR,
    COLLECTION_NAME,
    EVAL_EMBED_MODEL,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
)


def build_vector_db() -> None:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("[build_vector_db] OPENAI_API_KEY가 설정되지 않았습니다.")
        raise SystemExit(1)

    pdf_files = sorted(PDFS_DIR.glob("*.pdf"))
    if not pdf_files:
        print(f"[build_vector_db] {PDFS_DIR} 에 PDF 파일이 없습니다.")
        raise SystemExit(1)

    print(f"[build_vector_db] PDF {len(pdf_files)}개 로드 시작")

    docs = []
    for pdf_path in pdf_files:
        print(f"  로드 중: {pdf_path.name}")
        loader = PyPDFLoader(str(pdf_path))
        docs.extend(loader.load())

    print(f"[build_vector_db] 총 {len(docs)}페이지 로드 완료, 청크 분할 중...")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    chunks = splitter.split_documents(docs)
    print(f"[build_vector_db] 총 {len(chunks)}개 청크 생성")

    VECTOR_STORE_DIR.mkdir(parents=True, exist_ok=True)

    embeddings = OpenAIEmbeddings(model=EVAL_EMBED_MODEL, api_key=api_key)
    vector_db = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        collection_name=COLLECTION_NAME,
        persist_directory=str(VECTOR_STORE_DIR),
    )

    print(f"[build_vector_db] Vector DB 저장 완료: {VECTOR_STORE_DIR}")
    print(f"[build_vector_db] 컬렉션: {COLLECTION_NAME}, 청크 수: {len(chunks)}")


if __name__ == "__main__":
    build_vector_db()
