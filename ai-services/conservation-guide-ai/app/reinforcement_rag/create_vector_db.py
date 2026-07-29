from dotenv import load_dotenv
load_dotenv()

import os
from pathlib import Path

from langchain_chroma import Chroma
from langchain_community.document_loaders import PyPDFLoader
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

RAG_DIR = Path(__file__).resolve().parent
DOCS_DIR = RAG_DIR / "reinforcement_docs"
PERSIST_DIR = RAG_DIR / "vector_store" / "reinforcement"

EMBEDDING_MODEL = "text-embedding-3-small"
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
COLLECTION_NAME = "reinforcement_docs"


def _load_documents(pdf_paths: list) -> list:
    documents = []
    for pdf_path in pdf_paths:
        documents.extend(PyPDFLoader(str(pdf_path)).load())
    return documents


# reinforcement_docs의 PDF들을 청크로 분할/임베딩해서 Chroma 벡터 DB로 구축.
def create_vector_db() -> Chroma:
    pdf_paths = sorted(DOCS_DIR.glob("*.pdf"))
    if not pdf_paths:
        raise FileNotFoundError(f"{DOCS_DIR}에 PDF 파일이 없습니다.")

    documents = _load_documents(pdf_paths)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    chunks = splitter.split_documents(documents)

    embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL, api_key=os.environ["OPENAI_API_KEY"])

    vector_db = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=str(PERSIST_DIR),
        collection_name=COLLECTION_NAME,
    )

    print(f"[reinforcement_rag] PDF {len(pdf_paths)}개 -> {len(documents)}페이지 -> "
          f"{len(chunks)}개 청크 임베딩 완료.")
    print(f"[reinforcement_rag] 저장 위치: {PERSIST_DIR}")

    return vector_db


if __name__ == "__main__":
    create_vector_db()
