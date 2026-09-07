"""문헌 기반 Golden Set 자동 생성 스크립트.

강화제·용매 관련 쿼리로 Chroma 유사도 검색을 해서 후보 청크만 추려낸 뒤,
여러 청크를 한 번의 LLM 호출로 일괄 처리. 전체 청크를 LLM에 돌리지 않습니다.

사용법 (conservation-guide-ai/ 디렉터리에서):
    python -m HyDE_evaluation.build_golden_set
    python -m HyDE_evaluation.build_golden_set --force       # 기존 파일 덮어쓰기
    python -m HyDE_evaluation.build_golden_set --top-k 30   # 쿼리당 청크 수 (기본 20)
    python -m HyDE_evaluation.build_golden_set --batch 5    # 배치당 청크 수 (기본 5)
"""
from __future__ import annotations

import argparse
import json
import os

from dotenv import load_dotenv
load_dotenv()

from langchain_chroma import Chroma
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from .config import (
    VECTOR_STORE_DIR,
    COLLECTION_NAME,
    EVAL_EMBED_MODEL,
    EVAL_LLM_MODEL,
    GOLDEN_SET_PATH,
)

VALID_AGENTS = ["Paraloid B72", "HPC", "폴리비닐부티랄", "수용성 Emulsion", "Paraloid NAD-10"]
VALID_SOLVENTS = ["아세톤", "톨루엔", "자일렌", "에틸아세테이트", "이소프로판올", "에탄올",
                  "MEK", "아밀아세테이트", "메탄올", "물", "나프타", "화이트스피릿"]

# 강화제/용매 추천이 실려 있을 법한 다양한 쿼리 — Chroma 유사도 검색에 사용
SEARCH_QUERIES = [
    # ── 재질별 (기본) ──
    "강화제 추천 토기 유물 Paraloid 아세톤",
    "강화제 용매 목재 HPC 에탄올",
    "금속 유물 강화처리 강화제 용매",
    "지류 종이 강화 처리 강화제",
    "석재 돌 유물 강화제 처리",

    # ── 재질별 (확장) ──
    "청자 백자 도자기 보존처리 강화제 용매",
    "철기 청동 금속 부식 강화처리 강화제",
    "골각기 뼈 상아 유물 강화처리",
    "섬유 직물 유기물 유물 강화 처리",
    "출토 토기 도토기 심발형 강화처리 강화제",

    # ── 강화제별 ──
    "Paraloid B72 사용 방법 농도 희석",
    "Paraloid B72 토기 도자기 강화처리 아세톤 톨루엔",
    "HPC 강화제 에탄올 물 목재 유기물",
    "수용성 에멀전 Emulsion 강화처리 물",
    "폴리비닐부티랄 강화처리 에탄올",
    "Paraloid NAD-10 나프타 화이트스피릿 비극성",

    # ── 손상 상태별 ──
    "박락 취약 유물 강화처리 강화제 선택",
    "균열 손상 유물 강화처리 방법",
    "출토 직후 응급처리 취약 유물 강화제",
    "표면 분해 열화 강화 처리 강화제",

    # ── 처리 방법·농도 ──
    "분무법 강화제 도포 처리 방법 순서",
    "침지법 강화처리 적용 유물",
    "강화제 농도 희석 비율 처리 횟수",
    "강화제 용매 증발 건조 처리 주의사항",

    # ── 문헌 특성 반영 ──
    "문화재수리 표준시방서 강화처리 재질별",
    "보존처리 보고서 강화처리 사례 강화제",
    "유물 재질별 강화제 용매 권장",
    "보존처리 강화단계 약품 선택",
    "강화처리 강화제 종류 선택 기준",
]

BATCH_EXTRACT_PROMPT = """아래는 문화재 보존처리 문헌에서 가져온 청크들입니다.
각 청크에서, 특정 재질의 문화재에 대해 명시적으로 권장하는 강화제와 용매 조합만 추출하세요.
명시적 권장이 없는 청크는 건너뛰어도 됩니다.

가능한 강화제: {agents}
가능한 용매: {solvents}

---
{chunks_block}
---

출력 형식 (JSON 배열, 추출된 항목이 없으면 빈 배열 []):
[
  {{
    "material": "재질명(예: 토기, 목재, 금속, 지류, 석재, 도자기)",
    "period": "유물의 시대 정보 (문헌에 언급된 경우만, 없으면 빈 문자열)",
    "current_status": "유물의 현재 상태 설명 (손상 정도·박락·균열·오염 등, 문헌에 언급된 경우만, 없으면 빈 문자열)",
    "agent": "강화제명",
    "solvent": "용매명",
    "source": "출처 파일명",
    "page": 페이지번호(정수),
    "excerpt": "강화제·용매 권장 근거가 된 문헌 원문 발췌 (60자 이내)"
  }}
]

주의: 명시적으로 권장된 것만 추출하고 추론하지 마세요. period와 current_status는 문헌에 실제로 언급된 경우에만 채우세요."""


def _build_chunks_block(chunks: list[dict]) -> str:
    lines = []
    for i, c in enumerate(chunks, 1):
        lines.append(f"[청크 {i}] 출처: {c['source']} p.{c['page']}")
        lines.append(c["content"])
        lines.append("")
    return "\n".join(lines)


def _extract_batch(chunks: list[dict], api_key: str) -> list[dict]:
    llm = ChatOpenAI(model=EVAL_LLM_MODEL, temperature=0, api_key=api_key)
    prompt = BATCH_EXTRACT_PROMPT.format(
        agents=", ".join(VALID_AGENTS),
        solvents=", ".join(VALID_SOLVENTS),
        chunks_block=_build_chunks_block(chunks),
    )
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        text = response.content.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        entries = json.loads(text)
        if not isinstance(entries, list):
            return []
        result = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            if e.get("agent") not in VALID_AGENTS:
                continue
            if e.get("solvent") not in VALID_SOLVENTS:
                continue
            result.append({
                "material": e.get("material", ""),
                "period": e.get("period", ""),
                "current_status": e.get("current_status", ""),
                "agent": e["agent"],
                "solvent": e["solvent"],
                "source": e.get("source", ""),
                "page": e.get("page", 0),
                "excerpt": e.get("excerpt", ""),
            })
        return result
    except Exception:
        return []


def _collect_candidate_chunks(
    vector_db: Chroma,
    top_k: int,
) -> list[dict]:
    """여러 쿼리로 유사도 검색 후 중복 제거해 후보 청크 목록 반환."""
    seen_contents: set[str] = set()
    candidates: list[dict] = []

    for query in SEARCH_QUERIES:
        docs = vector_db.similarity_search(query, k=top_k)
        for doc in docs:
            key = doc.page_content[:100]  # 앞 100자로 중복 판단
            if key in seen_contents:
                continue
            seen_contents.add(key)
            candidates.append({
                "content": doc.page_content,
                "source": doc.metadata.get("source", ""),
                "page": doc.metadata.get("page", 0),
            })

    return candidates


def _build_golden_set(all_entries: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    golden_set: list[dict] = []
    idx_counter: dict[str, int] = {}

    for entry in all_entries:
        key = (entry["material"], entry["agent"], entry["solvent"])
        if key in seen:
            for gs in golden_set:
                if (gs["relic_info"]["material"] == entry["material"]
                        and gs["golden"]["agent"] == entry["agent"]
                        and gs["golden"]["solvent"] == entry["solvent"]):
                    gs["source_evidence"].append({
                        "source": entry["source"],
                        "page": entry["page"],
                        "excerpt": entry["excerpt"],
                    })
                    break
            continue

        seen.add(key)
        mat = entry["material"]
        idx_counter[mat] = idx_counter.get(mat, 0) + 1
        case_id = f"{mat}_{idx_counter[mat]:02d}"

        relic_info: dict = {"material": mat}
        if entry.get("period"):
            relic_info["period"] = entry["period"]
        if entry.get("current_status"):
            relic_info["current_status"] = entry["current_status"]

        golden_set.append({
            "id": case_id,
            "relic_info": relic_info,
            "golden": {
                "agent": entry["agent"],
                "solvent": entry["solvent"],
            },
            "source_evidence": [{
                "source": entry["source"],
                "page": entry["page"],
                "excerpt": entry["excerpt"],
            }],
        })

    return golden_set


def build_golden_set() -> None:
    parser = argparse.ArgumentParser(description="문헌 기반 Golden Set 생성")
    parser.add_argument("--force", action="store_true", help="기존 golden_set.json 덮어쓰기")
    parser.add_argument("--top-k", type=int, default=20,
                        help="쿼리당 Chroma 검색 청크 수 (기본값: 20)")
    parser.add_argument("--batch", type=int, default=5,
                        help="LLM 배치당 청크 수 (기본값: 5)")
    args = parser.parse_args()

    if GOLDEN_SET_PATH.exists() and not args.force:
        print(f"[build_golden_set] golden_set.json 이 이미 존재합니다: {GOLDEN_SET_PATH}")
        print("  덮어쓰려면 --force 옵션을 사용하세요.")
        raise SystemExit(0)

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("[build_golden_set] OPENAI_API_KEY가 설정되지 않았습니다.")
        raise SystemExit(1)

    if not VECTOR_STORE_DIR.exists():
        print(f"[build_golden_set] eval_vector_store/ 가 없습니다: {VECTOR_STORE_DIR}")
        print("  먼저 build_vector_db.py를 실행하세요.")
        raise SystemExit(1)

    embeddings = OpenAIEmbeddings(model=EVAL_EMBED_MODEL, api_key=api_key)
    vector_db = Chroma(
        persist_directory=str(VECTOR_STORE_DIR),
        embedding_function=embeddings,
        collection_name=COLLECTION_NAME,
    )

    print(f"[build_golden_set] {len(SEARCH_QUERIES)}개 쿼리로 후보 청크 검색 중 (top_k={args.top_k})...")
    candidates = _collect_candidate_chunks(vector_db, args.top_k)
    print(f"[build_golden_set] 후보 청크 {len(candidates)}개 확보 (중복 제거 후)")

    batch_size = args.batch
    total_batches = (len(candidates) + batch_size - 1) // batch_size
    all_entries: list[dict] = []

    print(f"[build_golden_set] LLM 추출 시작 ({total_batches}배치 × 최대 {batch_size}청크)\n")
    for b_idx in range(0, len(candidates), batch_size):
        batch = candidates[b_idx: b_idx + batch_size]
        batch_num = b_idx // batch_size + 1
        print(f"  [배치 {batch_num}/{total_batches}] {len(batch)}개 청크 처리 중...", end=" ", flush=True)
        entries = _extract_batch(batch, api_key)
        if entries:
            print(f"{len(entries)}건 추출")
            all_entries.extend(entries)
        else:
            print("없음")

    golden_set = _build_golden_set(all_entries)

    GOLDEN_SET_PATH.write_text(
        json.dumps(golden_set, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n[build_golden_set] {len(golden_set)}개 케이스 저장 완료: {GOLDEN_SET_PATH}")
    print("  → golden_set.json 을 검수한 뒤 runner.py 를 실행하세요.")


if __name__ == "__main__":
    build_golden_set()
