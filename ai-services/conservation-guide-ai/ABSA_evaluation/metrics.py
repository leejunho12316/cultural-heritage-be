"""결과 JSON 집계 및 리포트 출력 CLI.

사용법 (conservation-guide-ai/ 디렉터리에서):
    python -m ABSA_evaluation.metrics
    python -m ABSA_evaluation.metrics --results-dir ABSA_evaluation/results --min-valid-runs 3
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

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


def _most_common_pct(values: list[str]) -> float:
    if not values:
        return 0.0
    top_count = Counter(values).most_common(1)[0][1]
    return top_count / len(values) * 100


def _derive_gt_overall(gt: dict) -> str:
    """GT JSON(9 aspect → severity)에서 대표 overall_severity 도출.

    severe ∈ GT → "severe", moderate ∈ GT → "moderate", else → "mild"
    """
    severities = set(gt.values())
    if "severe" in severities:
        return "severe"
    if "moderate" in severities:
        return "moderate"
    return "mild"


def _load_results(results_dir: Path) -> list[dict]:
    paths = sorted(results_dir.glob("*_eval.json"))
    return [json.loads(p.read_text(encoding="utf-8")) for p in paths]


def compute_metrics(results: list[dict], min_valid_runs: int) -> dict:
    """pair별 일관도·정확도를 계산해 집계 통계를 반환."""

    # per-pair 수집용
    baseline_consistencies: list[float] = []
    absa_consistencies_per_aspect: dict[str, list[float]] = {a: [] for a in ASPECTS}
    absa_overall_consistencies: list[float] = []

    baseline_gt_accs: list[float] = []
    absa_gt_accs_per_aspect: dict[str, list[float]] = {a: [] for a in ASPECTS}
    absa_overall_gt_accs: list[float] = []

    warned_pairs: list[str] = []

    for r in results:
        pair_id = r["pair_id"]
        gt = r.get("ground_truth")

        # --- Baseline ---
        bl_valid = [
            run["result"]["overall_severity"]
            for run in r["baseline_runs"]
            if run["status"] == "ok"
        ]
        if len(bl_valid) < min_valid_runs:
            warned_pairs.append(f"{pair_id} (baseline valid={len(bl_valid)})")
        if bl_valid:
            baseline_consistencies.append(_most_common_pct(bl_valid))
            if gt:
                gt_overall = _derive_gt_overall(gt)
                baseline_gt_accs.append(
                    sum(s == gt_overall for s in bl_valid) / len(bl_valid) * 100
                )

        # --- ABSA ---
        ab_valid = [
            run["result"]
            for run in r["absa_runs"]
            if run["status"] == "ok"
        ]
        if len(ab_valid) < min_valid_runs:
            warned_pairs.append(f"{pair_id} (absa valid={len(ab_valid)})")
        if ab_valid:
            # per-aspect consistency
            for aspect in ASPECTS:
                sev_list = [res[aspect]["severity"] for res in ab_valid]
                absa_consistencies_per_aspect[aspect].append(_most_common_pct(sev_list))
                if gt:
                    absa_gt_accs_per_aspect[aspect].append(
                        sum(s == gt[aspect] for s in sev_list) / len(sev_list) * 100
                    )

            # overall_severity consistency
            overall_list = [res["overall_severity"] for res in ab_valid]
            absa_overall_consistencies.append(_most_common_pct(overall_list))

            # overall_severity GT accuracy
            if gt:
                gt_overall = _derive_gt_overall(gt)
                absa_overall_gt_accs.append(
                    sum(s == gt_overall for s in overall_list) / len(overall_list) * 100
                )

    def _mean(lst: list[float]) -> float | None:
        return sum(lst) / len(lst) if lst else None

    aspect_stats = {}
    for aspect in ASPECTS:
        consis_vals = absa_consistencies_per_aspect[aspect]
        acc_vals    = absa_gt_accs_per_aspect[aspect]
        aspect_stats[aspect] = {
            "consistency": _mean(consis_vals),
            "gt_accuracy":  _mean(acc_vals),
            "n_consistency": len(consis_vals),
            "n_gt_accuracy":  len(acc_vals),
        }

    # 9-aspect mean consistency (overall_severity 제외)
    aspect_consis_means = [
        v["consistency"] for v in aspect_stats.values() if v["consistency"] is not None
    ]
    absa_mean_consistency = _mean(aspect_consis_means)

    aspect_gt_means = [
        v["gt_accuracy"] for v in aspect_stats.values() if v["gt_accuracy"] is not None
    ]
    absa_mean_gt_accuracy = _mean(aspect_gt_means)

    # --- Judge 점수 집계 (judge.py 실행 후에만 존재) ---
    baseline_consistency_scores: list[float] = []
    baseline_richness_scores: list[float] = []
    absa_consistency_scores: list[float] = []
    absa_richness_scores: list[float] = []

    for r in results:
        js = r.get("judge_scores")
        if not js:
            continue
        bl = js.get("baseline", {})
        if bl.get("consistency_score") is not None:
            baseline_consistency_scores.append(bl["consistency_score"])
        if bl.get("richness_score") is not None:
            baseline_richness_scores.append(bl["richness_score"])
        ab_js = js.get("absa", {})
        if ab_js.get("consistency_score") is not None:
            absa_consistency_scores.append(ab_js["consistency_score"])
        if ab_js.get("richness_score") is not None:
            absa_richness_scores.append(ab_js["richness_score"])

    return {
        "n_pairs": len(results),
        "warned_pairs": warned_pairs,
        "baseline": {
            "consistency": _mean(baseline_consistencies),
            "gt_accuracy": _mean(baseline_gt_accs),
            "n_gt_pairs":  len(baseline_gt_accs),
            "judge_consistency": _mean(baseline_consistency_scores),
            "judge_richness":    _mean(baseline_richness_scores),
        },
        "absa": {
            "mean_consistency": absa_mean_consistency,
            "mean_gt_accuracy": absa_mean_gt_accuracy,
            "overall_severity_consistency": _mean(absa_overall_consistencies),
            "overall_severity_gt_accuracy": _mean(absa_overall_gt_accs),
            "n_gt_pairs": len(absa_overall_gt_accs),
            "judge_consistency": _mean(absa_consistency_scores),
            "judge_richness":    _mean(absa_richness_scores),
            "per_aspect": aspect_stats,
        },
    }


def print_report(metrics: dict, results_dir: Path) -> None:
    bl  = metrics["baseline"]
    ab  = metrics["absa"]
    n   = metrics["n_pairs"]

    def _fmt(v: float | None) -> str:
        return f"{v:.1f}%" if v is not None else "  N/A  "

    diff_cons = (
        (ab["mean_consistency"] - bl["consistency"])
        if ab["mean_consistency"] is not None and bl["consistency"] is not None
        else None
    )
    diff_acc = (
        (ab["mean_gt_accuracy"] - bl["gt_accuracy"])
        if ab["mean_gt_accuracy"] is not None and bl["gt_accuracy"] is not None
        else None
    )

    sep = "=" * 65
    print(sep)
    print("  Reinforcement Wetting — VLM Consistency & Accuracy Report")
    print(f"  Results: {results_dir}   Pairs: {n}")
    print(sep)

    if metrics["warned_pairs"]:
        print("\n[경고] valid run 수 부족:")
        for w in metrics["warned_pairs"]:
            print(f"  - {w}")

    print("\n--- Severity 값 평가 ---\n")
    print(f"{'Metric':<36} {'Baseline':>10}\t{'ABSA (9-aspect mean)':>20}")
    print("-" * 65)
    print(
        f"{'일관도 (Consistency)':<36} {_fmt(bl['consistency']):>10}\t"
        f"{_fmt(ab['mean_consistency']):>20}"
        + (f"   {diff_cons:+.1f}pp" if diff_cons is not None else "")
    )
    gt_label = f"Ground Truth (정답) 기반 정확도 (n={bl['n_gt_pairs']})"
    print(
        f"{gt_label:<36} {_fmt(bl['gt_accuracy']):>10}\t"
        f"{_fmt(ab['mean_gt_accuracy']):>20}"
        + (f"   {diff_acc:+.1f}pp" if diff_acc is not None else "")
    )

    # --- Judge 점수 섹션 ---
    has_judge = bl.get("judge_consistency") is not None or ab.get("judge_consistency") is not None

    if has_judge:
        def _fmt_score(v: float | None) -> str:
            return f"{v:.2f}/5" if v is not None else "  N/A"

        def _diff(a_val, b_val):
            return f"   {a_val - b_val:+.2f}pt" if a_val is not None and b_val is not None else ""

        print("\n--- description 텍스트 품질 (LLM Judge, 1~5점) ---\n")
        print(f"{'Metric':<32} {'Baseline':>8}\t{'ABSA (9-aspect mean)':>20}")
        print("-" * 65)
        print(
            f"{'일관성 (consistency)':<32} {_fmt_score(bl['judge_consistency']):>8}\t"
            f"{_fmt_score(ab['judge_consistency']):>20}"
            + _diff(ab["judge_consistency"], bl["judge_consistency"])
        )
        print(
            f"{'관점 다양성 (richness)':<32} {_fmt_score(bl['judge_richness']):>8}\t"
            f"{_fmt_score(ab['judge_richness']):>20}"
            + _diff(ab["judge_richness"], bl["judge_richness"])
        )

    print("\n--- Per-Aspect ABSA Detail (across all pairs) ---")
    for aspect, stat in ab["per_aspect"].items():
        cons_str = _fmt(stat["consistency"])
        acc_str  = _fmt(stat["gt_accuracy"])
        print(f"  {aspect:<25} consistency: {cons_str}   정답 기반 정확도: {acc_str}")

    print(f"\n  overall_severity  consistency: {_fmt(ab['overall_severity_consistency'])}"
          f"   정답 기반 정확도: {_fmt(ab['overall_severity_gt_accuracy'])}")
    print(sep)


def main() -> None:
    parser = argparse.ArgumentParser(description="ABSA 평가 결과 집계 및 리포트 출력")
    parser.add_argument("--results-dir", default=str(DEFAULT_RESULTS_DIR),
                        help="결과 JSON 디렉터리 (기본값: ABSA_evaluation/results)")
    parser.add_argument("--min-valid-runs", type=int, default=3,
                        help="pair당 유효 run 최솟값 (미달 시 경고, 기본값: 3)")
    args = parser.parse_args()

    results_dir = Path(args.results_dir).resolve()
    if not results_dir.exists():
        print(f"[metrics] 결과 디렉터리가 없습니다: {results_dir}")
        raise SystemExit(1)

    results = _load_results(results_dir)
    if not results:
        print(f"[metrics] *_eval.json 파일이 없습니다: {results_dir}")
        raise SystemExit(1)

    metrics = compute_metrics(results, min_valid_runs=args.min_valid_runs)
    print_report(metrics, results_dir)


if __name__ == "__main__":
    main()
