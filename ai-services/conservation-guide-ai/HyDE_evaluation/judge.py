"""LLM-as-judge: 검색 품질 + 답변 품질 채점 CLI.

runner.py 실행 후 results/ 의 *_eval.json 을 읽어
각 케이스의 Plain RAG / HyDE RAG 결과를 채점하고
judge_scores 필드를 추가해 덮어씁니다.

사용법 (conservation-guide-ai/ 디렉터리에서):
    python -m HyDE_evaluation.judge
    python -m HyDE_evaluation.judge --judge-model gpt-5.4-nano
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
load_dotenv()

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from .config import RESULTS_DIR, EVAL_JUDGE_MODEL


class JudgeResult(BaseModel):
    relevance_score: Literal[1, 2, 3, 4, 5]
    relevance_reason: str
    professionalism_score: Literal[1, 2, 3, 4, 5]
    professionalism_reason: str
    groundedness_score: Literal[1, 2, 3, 4, 5]
    groundedness_reason: str


JUDGE_RUBRIC = """아래는 문화재 보존처리 강화처리 강화제·용매 추천 시스템의 평가 데이터입니다.
질의, 검색된 문헌 청크, 그리고 추천 답변을 보고 세 가지 기준으로 각각 1~5점을 평가해주세요.

#질의
{query}

#검색된 문헌 청크 (K회 실행 전체)
{chunks_block}

#추천 답변 (K회 실행 전체)
{answers_block}

---

[기준 A] relevance_score — 검색 품질 (1~5점):
  주어진 질의에 대해 검색된 청크들이 얼마나 관련 있는가
  5: 모든 청크가 해당 재질/강화처리에 직접 관련된 전문 내용
  4: 대부분 관련, 일부 다소 일반적
  3: 절반 이상 관련
  2: 일부만 관련
  1: 대부분 무관

[기준 B1] professionalism_score — 답변 전문성 (1~5점):
  강화제 선택 근거가 재질 특성·강화제 특성을 구체적으로 연결해 설명하는가
  5: 재질과 강화제 특성의 연관성을 구체적 근거로 설명
  4: 근거가 있으나 일부 피상적
  3: 기본 근거는 있으나 구체성 부족
  2: 근거가 매우 피상적
  1: 근거 없이 결론만 서술

[기준 B2] groundedness_score — 문헌 반영도 (1~5점):
  제공된 참고 문헌 내용을 실제 답변에 반영했는가
  5: 문헌 내용을 구체적으로 인용하거나 직접 반영
  4: 문헌 방향성과 일치하나 직접 인용 없음
  3: 일부 반영
  2: 문헌과 무관하게 기본 지식만 활용
  1: 문헌과 상충하거나 전혀 활용 안 함"""


def _build_chunks_block(runs: list[dict]) -> str:
    blocks = []
    for run in runs:
        if run["status"] != "ok":
            continue
        lines = [f"[Run {run['run'] + 1}]"]
        for i, chunk in enumerate(run.get("chunks", []), 1):
            lines.append(f"  청크 {i} (출처: {chunk.get('source')} p.{chunk.get('page')}):")
            lines.append(f"  {chunk['content'][:200]}...")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else "(유효한 실행 없음)"


def _build_answers_block(runs: list[dict]) -> str:
    lines = []
    for run in runs:
        if run["status"] != "ok":
            continue
        r = run["result"]
        lines.append(
            f"[Run {run['run'] + 1}] 강화제={r['recommended_agent']}, "
            f"용매={r['recommended_solvent']}\n  근거: {r['reason']}"
        )
    return "\n\n".join(lines) if lines else "(유효한 실행 없음)"


def score_rag(
    query: str,
    runs: list[dict],
    api_key: str,
    judge_model: str,
) -> dict:
    valid = [r for r in runs if r["status"] == "ok"]
    if not valid:
        return {
            "relevance_score": None,
            "professionalism_score": None,
            "groundedness_score": None,
            "error": "valid run 없음",
        }

    llm = ChatOpenAI(model=judge_model, temperature=0, api_key=api_key)
    structured = llm.with_structured_output(JudgeResult)

    prompt = JUDGE_RUBRIC.format(
        query=query,
        chunks_block=_build_chunks_block(valid),
        answers_block=_build_answers_block(valid),
    )
    try:
        jr = structured.invoke([HumanMessage(content=prompt)])
        return {
            "relevance_score": jr.relevance_score,
            "relevance_reason": jr.relevance_reason,
            "professionalism_score": jr.professionalism_score,
            "professionalism_reason": jr.professionalism_reason,
            "groundedness_score": jr.groundedness_score,
            "groundedness_reason": jr.groundedness_reason,
        }
    except Exception as e:
        return {
            "relevance_score": None,
            "professionalism_score": None,
            "groundedness_score": None,
            "error": str(e),
        }


def judge_case(result: dict, api_key: str, judge_model: str) -> dict:
    query = result["query"]

    print("    [judge] plain_rag...", end=" ", flush=True)
    plain_scores = score_rag(query, result["plain_rag_runs"], api_key, judge_model)
    print(f"relevance={plain_scores.get('relevance_score')}  "
          f"professionalism={plain_scores.get('professionalism_score')}  "
          f"groundedness={plain_scores.get('groundedness_score')}")

    print("    [judge] hyde_rag...", end=" ", flush=True)
    hyde_scores = score_rag(query, result["hyde_rag_runs"], api_key, judge_model)
    print(f"relevance={hyde_scores.get('relevance_score')}  "
          f"professionalism={hyde_scores.get('professionalism_score')}  "
          f"groundedness={hyde_scores.get('groundedness_score')}")

    result["judge_scores"] = {
        "plain_rag": plain_scores,
        "hyde_rag": hyde_scores,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="HyDE RAG vs Plain RAG LLM-as-judge 채점")
    parser.add_argument("--results-dir", default=str(RESULTS_DIR),
                        help="결과 JSON 디렉터리")
    parser.add_argument("--judge-model", default=EVAL_JUDGE_MODEL,
                        help=f"채점용 LLM 모델 (기본값: {EVAL_JUDGE_MODEL})")
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("[judge] OPENAI_API_KEY가 설정되지 않았습니다.")
        raise SystemExit(1)

    results_dir = Path(args.results_dir)
    paths = sorted(results_dir.glob("*_eval.json"))
    if not paths:
        print(f"[judge] *_eval.json 파일이 없습니다: {results_dir}")
        raise SystemExit(1)

    print(f"[judge] {len(paths)}개 케이스 채점 시작  |  judge_model={args.judge_model}\n")

    for path in paths:
        result = json.loads(path.read_text(encoding="utf-8"))
        print(f"  case: {result['test_id']}")
        result = judge_case(result, api_key, args.judge_model)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  → 저장: {path}\n")

    print("[judge] 완료!")


if __name__ == "__main__":
    main()
