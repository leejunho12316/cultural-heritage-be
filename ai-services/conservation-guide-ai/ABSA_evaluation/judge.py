"""근거 텍스트 일관성 LLM 채점 CLI.

runner.py 실행 후 results/ 디렉터리의 *_eval.json을 읽어
각 pair의 description 일관성을 1~5점으로 채점하고
*_eval.json에 judge_scores 필드를 추가해 덮어씁니다.

사용법 (conservation-guide-ai/ 디렉터리에서):
    python -m ABSA_evaluation.judge
    python -m ABSA_evaluation.judge --judge-model gpt-4o --results-dir ABSA_evaluation/results
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
load_dotenv()

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from .config import EVAL_VISION_MODEL

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_RESULTS_DIR = EVAL_DIR / "results"

ASPECTS = [
    "hue_shift",
    "brightness_change",
    "saturation_change",
    "gloss_change",
    "blanching",
    "uneven_penetration",
    "edge_visibility",
    "crack_response",
    "texture_change",
]

ASPECT_LABELS = {
    "hue_shift":           "색조 변화",
    "brightness_change":   "명도 변화",
    "saturation_change":   "채도 변화",
    "gloss_change":        "광택 변화",
    "blanching":           "백화현상",
    "uneven_penetration":  "얼룩/불균일 침투",
    "edge_visibility":     "처리 경계 뚜렷함",
    "crack_response":      "균열부 반응",
    "texture_change":      "질감 변화",
}

# 채점에는 vision이 필요 없으므로 기본값을 gpt-4o로 설정
DEFAULT_JUDGE_MODEL = "gpt-5.4-nano"

SCORE_RUBRIC = """다음은 같은 이미지 쌍에 대해 여러 번 독립적으로 분석한 결과의 설명문들입니다.
아래 두 가지 기준으로 각각 1~5점을 평가해주세요.

{descriptions_block}

---

[기준 A] 일관성 (consistency_score): 설명들이 서로 얼마나 같은 관찰 내용과 근거를 공유하는가
5점: 모든 설명이 동일한 관찰 내용과 근거를 공유함 (표현만 다를 뿐 내용 완전 일치)
4점: 핵심 관찰은 같으나 일부 세부 내용이나 강조점에 차이가 있음
3점: 주요 관찰 방향은 비슷하나 일부 모순되거나 누락된 내용이 존재
2점: 설명들이 부분적으로만 겹치고 서로 다른 관찰을 강조함
1점: 설명들이 서로 모순되거나 전혀 다른 관찰 내용을 담고 있음

[기준 B] 관점 다양성 (richness_score): 각 설명이 얼마나 다양한 관점(색조, 명도, 채도, 광택, 백화현상, 얼룩/불균일 침투, 처리 경계 뚜렷함, 균열부 반응, 질감 변화)을 포함해 근거를 제시하는가
5점: 7가지 이상의 관점을 구체적인 근거와 함께 고루 서술함
4점: 3~6가지 관점을 구체적으로 서술하나 일부 관점이 빠짐
3점: 3~6가지 관점만 언급하거나 근거가 다소 부족함
2점: 1~2가지 관점만 서술하거나 근거가 매우 피상적임
1점: 구체적인 관찰 근거 없이 결론만 서술함
"""



class JudgeResult(BaseModel):
    consistency_score: Literal[1, 2, 3, 4, 5]
    consistency_reason: str
    richness_score: Literal[1, 2, 3, 4, 5]
    richness_reason: str


def _build_descriptions_block(descriptions: list[str]) -> str:
    return "\n\n".join(
        f"[설명 {i + 1}]:\n{desc}" for i, desc in enumerate(descriptions)
    )


def _combine_absa_run(run_result: dict) -> str:
    """ABSA 실행 결과 1건의 9개 속성 description을 하나의 텍스트 블록으로 합친다."""
    lines = [
        f"- {ASPECT_LABELS[aspect]}: {run_result[aspect]['description']}"
        for aspect in ASPECTS
    ]
    return "\n".join(lines)


def score_consistency(
    descriptions: list[str],
    api_key: str,
    judge_model: str = DEFAULT_JUDGE_MODEL,
) -> JudgeResult:
    """K개의 description을 받아 텍스트 일관성을 1~5점으로 채점."""
    if len(descriptions) < 2:
        return JudgeResult(score=5, reason="비교할 설명이 1개뿐이므로 최고점 부여")

    llm = ChatOpenAI(model=judge_model, temperature=0, api_key=api_key)
    structured = llm.with_structured_output(JudgeResult)

    prompt = SCORE_RUBRIC.format(
        descriptions_block=_build_descriptions_block(descriptions)
    )
    return structured.invoke([HumanMessage(content=prompt)])


def judge_pair(result: dict, api_key: str, judge_model: str) -> dict:
    """pair 결과 dict에 judge_scores 필드를 추가해 반환."""
    judge_scores = {}

    # --- Baseline: overall description ---
    bl_descriptions = [
        run["result"]["description"]
        for run in result["baseline_runs"]
        if run["status"] == "ok"
    ]
    if bl_descriptions:
        print("    [judge] baseline description...", end=" ", flush=True)
        try:
            jr = score_consistency(bl_descriptions, api_key, judge_model)
            judge_scores["baseline"] = {
                "consistency_score": jr.consistency_score,
                "consistency_reason": jr.consistency_reason,
                "richness_score": jr.richness_score,
                "richness_reason": jr.richness_reason,
            }
            print(f"consistency={jr.consistency_score}  richness={jr.richness_score}")
        except Exception as e:
            judge_scores["baseline"] = {"consistency_score": None, "richness_score": None, "error": str(e)}
            print(f"error — {e}")
    else:
        judge_scores["baseline"] = {"consistency_score": None, "richness_score": None, "error": "valid run 없음"}

    # --- ABSA: K번 실행의 9개 속성 전체를 run별로 합쳐서 한 번에 채점 ---
    absa_valid = [
        run["result"] for run in result["absa_runs"] if run["status"] == "ok"
    ]

    if absa_valid:
        # 각 run의 9개 description을 하나의 블록으로 합침
        combined_descriptions = [_combine_absa_run(res) for res in absa_valid]
        print("    [judge] absa (9개 속성 통합)...", end=" ", flush=True)
        try:
            jr = score_consistency(combined_descriptions, api_key, judge_model)
            judge_scores["absa"] = {
                "consistency_score": jr.consistency_score,
                "consistency_reason": jr.consistency_reason,
                "richness_score": jr.richness_score,
                "richness_reason": jr.richness_reason,
            }
            print(f"consistency={jr.consistency_score}  richness={jr.richness_score}")
        except Exception as e:
            judge_scores["absa"] = {"consistency_score": None, "richness_score": None, "error": str(e)}
            print(f"error — {e}")
    else:
        judge_scores["absa"] = {"consistency_score": None, "richness_score": None, "error": "valid run 없음"}

    result["judge_scores"] = judge_scores
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="근거 텍스트 일관성 LLM 채점")
    parser.add_argument("--results-dir", default=str(DEFAULT_RESULTS_DIR),
                        help="결과 JSON 디렉터리 (기본값: ABSA_evaluation/results)")
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL,
                        help=f"채점용 LLM 모델 (기본값: {DEFAULT_JUDGE_MODEL})")
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("[judge] OPENAI_API_KEY가 설정되지 않았습니다.")
        raise SystemExit(1)

    results_dir = Path(args.results_dir).resolve()
    paths = sorted(results_dir.glob("*_eval.json"))
    if not paths:
        print(f"[judge] *_eval.json 파일이 없습니다: {results_dir}")
        raise SystemExit(1)

    print(f"[judge] {len(paths)}개 pair 채점 시작  |  judge_model={args.judge_model}\n")

    for path in paths:
        result = json.loads(path.read_text(encoding="utf-8"))
        print(f"  pair: {result['pair_id']}")
        result = judge_pair(result, api_key, args.judge_model)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  → 저장: {path}\n")

    print("[judge] 완료!")


if __name__ == "__main__":
    main()
