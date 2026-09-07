"""eval_vector_store 청크 브라우저.

사용법:
    py temp.py
    py temp.py --source 보존처리  # 파일명에 키워드가 포함된 청크만
    py temp.py --keyword 강화제   # 내용에 키워드가 포함된 청크만
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings

VECTOR_STORE_DIR = Path(__file__).parent / "HyDE_evaluation" / "eval_vector_store"
COLLECTION_NAME = "hyde_eval_docs"


def main() -> None:
    parser = argparse.ArgumentParser(description="eval_vector_store 청크 브라우저")
    parser.add_argument("--source", default="", help="파일명 필터 (부분 일치)")
    parser.add_argument("--keyword", default="", help="청크 내용 필터 (부분 일치)")
    parser.add_argument("--start", type=int, default=0, help="시작 인덱스")
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY", "")
    db = Chroma(
        persist_directory=str(VECTOR_STORE_DIR),
        embedding_function=OpenAIEmbeddings(model="text-embedding-3-small", api_key=api_key),
        collection_name=COLLECTION_NAME,
    )

    data = db.get(include=["documents", "metadatas"])
    documents = data["documents"]
    metadatas = data["metadatas"]

    # 필터링
    chunks = [
        (doc, meta)
        for doc, meta in zip(documents, metadatas)
        if (not args.source or args.source in (meta.get("source") or ""))
        and (not args.keyword or args.keyword in doc)
    ]

    total = len(chunks)
    print(f"총 {total}개 청크 (전체 {len(documents)}개 중)\n")
    print("조작: Enter=다음  p=이전  q=종료  g<번호>=이동 (예: g100)")

    i = max(0, min(args.start, total - 1))
    while 0 <= i < total:
        doc, meta = chunks[i]
        source = Path(meta.get("source") or "").name
        page = meta.get("page", "?")

        print("\n" + "=" * 70)
        print(f"[{i + 1}/{total}]  {source}  p.{page}")
        print("-" * 70)
        print(doc)
        print("=" * 70)

        cmd = input(">> ").strip().lower()
        if cmd == "q":
            break
        elif cmd == "p":
            i = max(0, i - 1)
        elif cmd.startswith("g"):
            try:
                i = int(cmd[1:]) - 1
            except ValueError:
                pass
        else:
            i += 1


if __name__ == "__main__":
    main()
