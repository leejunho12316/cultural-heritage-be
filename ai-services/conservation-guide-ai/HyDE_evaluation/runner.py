"""HyDE RAG vs Plain RAG 평가 실행 CLI.

golden_set.json 의 각 테스트 케이스에 대해 K회 반복하며
Plain RAG / HyDE RAG 결과를 results/{test_id}_eval.json 으로 저장.

사용법 (conservation-guide-ai/ 디렉터리에서):
    python -m HyDE_evaluation.runner --k 5
    python -m HyDE_evaluation.runner --k 1 --model gpt-5.4-nano --temperature 0.7
"""
from __future__ import annotations

import argparse
import json
import os
import traceback
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from .config import (
    GOLDEN_SET_PATH,
    RESULTS_DIR,
    EVAL_LLM_MODEL,
    EVAL_TEMPERATURE,
    DEFAULT_K,
)
from .callers import retrieve_plain_rag, retrieve_hyde_rag, call_recommendation


def _run_case(
    case: dict,
    k: int,
    api_key: str,
    model: str,
    temperature: float,
    output_dir: Path,
) -> None:
    test_id = case["id"]
    relic_info = case["relic_info"]
    golden = case["golden"]
    query = f"유물 정보 {relic_info}에 적합한 강화처리용 강화제와 유기용매 추천"

    result = {
        "test_id": test_id,
        "relic_info": relic_info,
        "golden": golden,
        "query": query,
        "k": k,
        "model": model,
        "temperature": temperature,
        "plain_rag_runs": [],
        "hyde_rag_runs": [],
    }

    for run_idx in range(k):
        print(f"  [plain_rag] run {run_idx + 1}/{k}...", end=" ", flush=True)
        try:
            chunks = retrieve_plain_rag(query, api_key)
            rec = call_recommendation(chunks, relic_info, api_key, model, temperature)
            result["plain_rag_runs"].append({
                "run": run_idx,
                "status": "ok",
                "chunks": chunks,
                "result": rec.model_dump(),
            })
            print("ok")
        except Exception as e:
            result["plain_rag_runs"].append({
                "run": run_idx,
                "status": "error",
                "error": f"{type(e).__name__}: {e}",
            })
            print(f"error — {e}")

        print(f"  [hyde_rag]  run {run_idx + 1}/{k}...", end=" ", flush=True)
        try:
            hypo_doc, chunks = retrieve_hyde_rag(query, api_key, model)
            rec = call_recommendation(chunks, relic_info, api_key, model, temperature)
            result["hyde_rag_runs"].append({
                "run": run_idx,
                "status": "ok",
                "hypothetical_doc": hypo_doc,
                "chunks": chunks,
                "result": rec.model_dump(),
            })
            print("ok")
        except Exception as e:
            result["hyde_rag_runs"].append({
                "run": run_idx,
                "status": "error",
                "error": f"{type(e).__name__}: {e}",
            })
            print(f"error — {e}")

    safe_id = test_id.replace("/", "_").replace("\\", "_").replace(":", "_")
    out_path = output_dir / f"{safe_id}_eval.json"
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  → 저장: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="HyDE RAG vs Plain RAG 평가 실행")
    parser.add_argument("--k", type=int, default=DEFAULT_K,
                        help=f"반복 실행 횟수 (기본값: {DEFAULT_K})")
    parser.add_argument("--model", default=EVAL_LLM_MODEL,
                        help=f"LLM 모델명 (기본값: {EVAL_LLM_MODEL})")
    parser.add_argument("--temperature", type=float, default=EVAL_TEMPERATURE,
                        help=f"temperature (기본값: {EVAL_TEMPERATURE})")
    parser.add_argument("--golden-set", default=str(GOLDEN_SET_PATH),
                        help="golden_set.json 경로")
    parser.add_argument("--output-dir", default=str(RESULTS_DIR),
                        help="결과 JSON 저장 경로")
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("[runner] OPENAI_API_KEY가 설정되지 않았습니다.")
        raise SystemExit(1)

    golden_set_path = Path(args.golden_set)
    if not golden_set_path.exists():
        print(f"[runner] golden_set.json 이 없습니다: {golden_set_path}")
        print("  먼저 build_golden_set.py를 실행해 Golden Set을 생성하고 검수하세요.")
        raise SystemExit(1)

    golden_set = json.loads(golden_set_path.read_text(encoding="utf-8"))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[runner] 테스트 케이스: {len(golden_set)}개  |  K={args.k}  |  model={args.model}")

    for case in golden_set:
        print(f"\n[runner] case: {case['id']}  ({case['relic_info']})")
        try:
            _run_case(
                case=case,
                k=args.k,
                api_key=api_key,
                model=args.model,
                temperature=args.temperature,
                output_dir=output_dir,
            )
        except Exception:
            print(f"[runner] 오류 발생 — {case['id']}")
            traceback.print_exc()

    print("\n[runner] 완료!")


if __name__ == "__main__":
    main()
