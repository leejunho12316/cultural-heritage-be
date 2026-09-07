"""평가 결과 집계 및 리포트 출력 CLI.

runner.py + judge.py 실행 후 results/ 의 *_eval.json 을 읽어
3가지 지표(Golden Set 정확도 / 검색 품질 / 답변 품질)를 집계하고 출력.

사용법 (conservation-guide-ai/ 디렉터리에서):
    python -m HyDE_evaluation.metrics
    python -m HyDE_evaluation.metrics --results-dir HyDE_evaluation/results
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import RESULTS_DIR, COMPATIBLE_SOLVENTS


def _accuracy(runs: list[dict], golden: dict) -> dict:
    """K회 실행에서 agent/solvent 정답률 반환.

    - agent: golden agent와 exact match
    - solvent: agent가 맞았을 때, 해당 강화제의 허용 용매 목록 안에 들어오면 정답
    """
    agent_correct = 0
    solvent_correct = 0
    both_correct = 0
    valid = 0
    for run in runs:
        if run["status"] != "ok":
            continue
        valid += 1
        r = run["result"]
        a_ok = r["recommended_agent"] == golden["agent"]
        allowed_solvents = COMPATIBLE_SOLVENTS.get(golden["agent"], [golden["solvent"]])
        s_ok = a_ok and r["recommended_solvent"] in allowed_solvents
        if a_ok:
            agent_correct += 1
        if s_ok:
            solvent_correct += 1
        if a_ok and s_ok:
            both_correct += 1
    if valid == 0:
        return {"agent": None, "solvent": None, "both": None, "valid": 0}
    return {
        "agent": agent_correct / valid * 100,
        "solvent": solvent_correct / valid * 100,
        "both": both_correct / valid * 100,
        "valid": valid,
    }


def _avg(values: list[float | None]) -> float | None:
    cleaned = [v for v in values if v is not None]
    return sum(cleaned) / len(cleaned) if cleaned else None


def _fmt(v: float | None, suffix: str = "") -> str:
    return f"{v:.1f}{suffix}" if v is not None else "N/A"


def main() -> None:
    parser = argparse.ArgumentParser(description="HyDE RAG vs Plain RAG 평가 리포트")
    parser.add_argument("--results-dir", default=str(RESULTS_DIR))
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    paths = sorted(results_dir.glob("*_eval.json"))
    if not paths:
        print(f"[metrics] *_eval.json 파일이 없습니다: {results_dir}")
        raise SystemExit(1)

    cases = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    k_val = cases[0].get("k", "?") if cases else "?"

    plain_agent, plain_solvent, plain_both = [], [], []
    hyde_agent, hyde_solvent, hyde_both = [], [], []
    plain_relevance, plain_professionalism, plain_groundedness = [], [], []
    hyde_relevance, hyde_professionalism, hyde_groundedness = [], [], []

    for case in cases:
        golden = case.get("golden", {})
        pa = _accuracy(case.get("plain_rag_runs", []), golden)
        ha = _accuracy(case.get("hyde_rag_runs", []), golden)
        plain_agent.append(pa["agent"])
        plain_solvent.append(pa["solvent"])
        plain_both.append(pa["both"])
        hyde_agent.append(ha["agent"])
        hyde_solvent.append(ha["solvent"])
        hyde_both.append(ha["both"])

        js = case.get("judge_scores", {})
        p_js = js.get("plain_rag", {})
        h_js = js.get("hyde_rag", {})
        plain_relevance.append(p_js.get("relevance_score"))
        plain_professionalism.append(p_js.get("professionalism_score"))
        plain_groundedness.append(p_js.get("groundedness_score"))
        hyde_relevance.append(h_js.get("relevance_score"))
        hyde_professionalism.append(h_js.get("professionalism_score"))
        hyde_groundedness.append(h_js.get("groundedness_score"))

    p_agent = _avg(plain_agent)
    p_solvent = _avg(plain_solvent)
    p_both = _avg(plain_both)
    h_agent = _avg(hyde_agent)
    h_solvent = _avg(hyde_solvent)
    h_both = _avg(hyde_both)

    p_rel = _avg(plain_relevance)
    p_pro = _avg(plain_professionalism)
    p_grd = _avg(plain_groundedness)
    h_rel = _avg(hyde_relevance)
    h_pro = _avg(hyde_professionalism)
    h_grd = _avg(hyde_groundedness)

    def delta_pp(a, b):
        if a is None or b is None:
            return "N/A"
        return f"{b - a:+.1f}pp"

    def delta_pt(a, b):
        if a is None or b is None:
            return "N/A"
        return f"{b - a:+.2f}pt"

    sep = "=" * 65
    line = "-" * 65
    print(f"\n{sep}")
    print(f"  Reinforcement RAG — HyDE vs Plain RAG Evaluation Report")
    print(f"  Results: {results_dir}   Test Cases: {len(cases)}   K: {k_val}")
    print(f"{sep}")

    print(f"\n--- Golden Set 정확도 ---\n")
    print(f"{'Metric':<35} {'Plain RAG':>10} {'HyDE RAG':>10} {'Δ':>10}")
    print(line)
    print(f"{'강화제 정확도 (agent)':<35} {_fmt(p_agent, '%'):>10} {_fmt(h_agent, '%'):>10} {delta_pp(p_agent, h_agent):>10}")
    print(f"{'용매 정확도 (solvent)':<35} {_fmt(p_solvent, '%'):>10} {_fmt(h_solvent, '%'):>10} {delta_pp(p_solvent, h_solvent):>10}")
    print(f"{'둘 다 정확 (both correct)':<35} {_fmt(p_both, '%'):>10} {_fmt(h_both, '%'):>10} {delta_pp(p_both, h_both):>10}")

    print(f"\n--- 검색 품질 (LLM Judge, 1~5점) ---\n")
    print(f"{'Metric':<35} {'Plain RAG':>10} {'HyDE RAG':>10} {'Δ':>10}")
    print(line)
    print(f"{'관련성 (relevance)':<35} {_fmt(p_rel, '/5'):>10} {_fmt(h_rel, '/5'):>10} {delta_pt(p_rel, h_rel):>10}")

    print(f"\n--- 답변 품질 (LLM Judge, 1~5점) ---\n")
    print(f"{'Metric':<35} {'Plain RAG':>10} {'HyDE RAG':>10} {'Δ':>10}")
    print(line)
    print(f"{'전문성 (professionalism)':<35} {_fmt(p_pro, '/5'):>10} {_fmt(h_pro, '/5'):>10} {delta_pt(p_pro, h_pro):>10}")
    print(f"{'문헌 반영도 (groundedness)':<35} {_fmt(p_grd, '/5'):>10} {_fmt(h_grd, '/5'):>10} {delta_pt(p_grd, h_grd):>10}")

    print(f"\n{sep}\n")


if __name__ == "__main__":
    main()
