"""평가 실행 CLI.

사용법 (conservation-guide-ai/ 디렉터리에서):
    python -m ABSA_evaluation.runner --k 5
    python -m ABSA_evaluation.runner --k 1 --model gpt-4o --temperature 0.7
    python -m ABSA_evaluation.runner --relic-info '{"material":"토기"}' \\
        --confirmed-agent '{"agent":"Paraloid B72","solvent":"아세톤"}'
"""
from __future__ import annotations

import argparse
import json
import os
import re
import traceback
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from .config import EVAL_VISION_MODEL, EVAL_TEMPERATURE, DEFAULT_K
from .callers import call_absa, call_baseline

EVAL_DIR      = Path(__file__).resolve().parent
ORIGINALS_DIR = EVAL_DIR / "eval_original_photos"
DEFAULT_DATASET_DIR = EVAL_DIR / "eval_test_photos"
DEFAULT_OUTPUT_DIR  = EVAL_DIR / "results"


def _find_before_image(stem: str, originals_dir: Path) -> Path | None:
    for ext in (".jpg", ".jpeg", ".png"):
        p = originals_dir / f"{stem}{ext}"
        if p.exists():
            return p
    return None


def _run_pair(
    pair_id: str,
    before_path: Path,
    after_path: Path,
    gt_path: Path | None,
    k: int,
    relic_info: dict,
    confirmed_agent: dict,
    api_key: str,
    model: str,
    temperature: float,
    output_dir: Path,
) -> None:
    before_url = str(before_path)
    after_url  = str(after_path)

    ground_truth = None
    if gt_path and gt_path.exists():
        ground_truth = json.loads(gt_path.read_text(encoding="utf-8"))

    result = {
        "pair_id": pair_id,
        "before_image": str(before_path),
        "after_image": str(after_path),
        "ground_truth": ground_truth,
        "k": k,
        "relic_info": relic_info,
        "confirmed_agent": confirmed_agent,
        "baseline_runs": [],
        "absa_runs": [],
    }

    for run_idx in range(k):
        print(f"  [baseline] run {run_idx + 1}/{k}...", end=" ", flush=True)
        try:
            bl = call_baseline(
                before_urls=[before_url],
                after_urls=[after_url],
                relic_info=relic_info,
                confirmed_agent=confirmed_agent,
                api_key=api_key,
                model=model,
                temperature=temperature,
            )
            result["baseline_runs"].append({
                "run": run_idx, "status": "ok", "result": bl.model_dump()
            })
            print("ok")
        except Exception as e:
            result["baseline_runs"].append({
                "run": run_idx, "status": "error", "error": f"{type(e).__name__}: {e}"
            })
            print(f"error — {e}")

        print(f"  [absa]     run {run_idx + 1}/{k}...", end=" ", flush=True)
        try:
            ab = call_absa(
                before_urls=[before_url],
                after_urls=[after_url],
                relic_info=relic_info,
                confirmed_agent=confirmed_agent,
                api_key=api_key,
                model=model,
                temperature=temperature,
            )
            result["absa_runs"].append({
                "run": run_idx, "status": "ok", "result": ab.model_dump()
            })
            print("ok")
        except Exception as e:
            result["absa_runs"].append({
                "run": run_idx, "status": "error", "error": f"{type(e).__name__}: {e}"
            })
            print(f"error — {e}")

    out_path = output_dir / f"{pair_id}_eval.json"
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  → 저장: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="ABSA vs Baseline VLM 일관도 평가")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET_DIR),
                        help="eval_test_photos 경로 (기본값: ABSA_evaluation/eval_test_photos)")
    parser.add_argument("--originals", default=str(ORIGINALS_DIR),
                        help="eval_original_photos 경로 (기본값: ABSA_evaluation/eval_original_photos)")
    parser.add_argument("--k", type=int, default=DEFAULT_K,
                        help=f"반복 실행 횟수 (기본값: {DEFAULT_K})")
    parser.add_argument("--model", default=EVAL_VISION_MODEL,
                        help=f"VLM 모델명 (기본값: {EVAL_VISION_MODEL})")
    parser.add_argument("--temperature", type=float, default=EVAL_TEMPERATURE,
                        help=f"temperature (기본값: {EVAL_TEMPERATURE})")
    parser.add_argument("--relic-info", default="{}",
                        help='유물 정보 JSON 문자열 (예: \'{"material":"토기"}\')')
    parser.add_argument("--confirmed-agent", default="{}",
                        help='확정된 강화제/용매 JSON 문자열 (예: \'{"agent":"Paraloid B72","solvent":"아세톤"}\')')
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR),
                        help="결과 JSON 저장 경로 (기본값: ABSA_evaluation/results)")
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("[runner] OPENAI_API_KEY가 설정되지 않았습니다. .env 파일 또는 환경변수를 확인하세요.")
        raise SystemExit(1)

    dataset_dir  = Path(args.dataset).resolve()
    originals_dir = Path(args.originals).resolve()
    output_dir   = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    relic_info      = json.loads(args.relic_info)
    confirmed_agent = json.loads(args.confirmed_agent)

    after_images = sorted(dataset_dir.glob("*_after_generated.png"))
    if not after_images:
        print(f"[runner] {dataset_dir} 에 *_after_generated.png 파일이 없습니다.")
        print("         먼저 generate_dataset.py를 실행해 테스트 데이터를 생성하세요.")
        raise SystemExit(1)

    print(f"[runner] 발견된 이미지 쌍: {len(after_images)}개  |  K={args.k}  |  model={args.model}")

    for after_path in after_images:
        stem = after_path.stem.removesuffix("_after_generated")
        # {original_stem}_v00 형식일 경우 _v## 접미사를 제거해 원본 이미지 탐색
        base_stem = re.sub(r"_v\d+$", "", stem)
        before_path = _find_before_image(base_stem, originals_dir)

        if before_path is None:
            print(f"[runner] 경고: before 이미지를 찾을 수 없음 — {base_stem}, 스킵")
            continue

        gt_path = dataset_dir / f"{stem}_ground_truth.json"

        print(f"\n[runner] pair: {stem}")
        try:
            _run_pair(
                pair_id=stem,
                before_path=before_path,
                after_path=after_path,
                gt_path=gt_path,
                k=args.k,
                relic_info=relic_info,
                confirmed_agent=confirmed_agent,
                api_key=api_key,
                model=args.model,
                temperature=args.temperature,
                output_dir=output_dir,
            )
        except Exception:
            print(f"[runner] 오류 발생 — {stem}")
            traceback.print_exc()

    print("\n[runner] 완료!")


if __name__ == "__main__":
    main()
