"""
evaluate_completeness.py

사람이 눈으로 분류해둔 완전/파편 폴더를 정답(ground truth)으로 놓고,
Grounding DINO + SAM2로 마스크를 뽑은 뒤 completeness_classifier.py의
estimate_completeness()가 실제로 얼마나 맞히는지 평가한다.

Colab에서 실행하는 걸 전제로 한다 (heritage_corrison의
GroundedSAMDetector, pottery_analysis의 completeness_classifier를
sys.path로 불러와 쓴다 - pottery_analysis_colab.ipynb에서 이미 설정한
경로를 그대로 재사용).

사용법 (Colab 셀에서):
    !python evaluate_completeness.py \
        --complete-dir /content/drive/MyDrive/kt_bigp/pottery_analysis/완전 \
        --fragment-dir /content/drive/MyDrive/kt_bigp/pottery_analysis/파편 \
        --out-csv /content/drive/MyDrive/kt_bigp/pottery_analysis/eval_result.csv
"""

import argparse
import csv
import sys
from pathlib import Path

# Colab에서는 노트북이 자체적으로 sys.path를 설정하지만, 로컬(이 저장소)에서
# 바로 실행할 경우를 대비해 app/ 폴더(재정리 v12 이후 실제 코드 위치)를
# 추가해둔다. 이미 sys.path에 다른 경로가 잡혀 있으면 그쪽이 우선이라
# Colab 동작에는 영향 없음.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from models.grounded_sam_detector import GroundedSAMDetector
from completeness_classifier import estimate_completeness

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

POTTERY_TEXT_PROMPT = (
    "ceramic vessel. pottery. clay pot. "
    "ceramic fragment. broken pottery shard."
)


def evaluate_folder(
    folder: Path, true_label: str, detector: GroundedSAMDetector
) -> list[dict]:
    results = []
    image_paths = sorted(
        p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS
    )

    for image_path in image_paths:
        row = {"file": image_path.name, "true_label": true_label}

        try:
            regions = detector.predict(image_path=str(image_path))
        except Exception as error:
            row["pred_label"] = "탐지오류"
            row["error"] = str(error)
            results.append(row)
            continue

        if not regions:
            row["pred_label"] = "탐지실패"
            results.append(row)
            continue

        mask = regions[0]["mask"]
        estimate = estimate_completeness(mask)

        row["pred_label"] = estimate.guess
        row["symmetry_iou"] = estimate.symmetry_iou
        row["circularity"] = estimate.circularity
        row["max_defect_depth_ratio"] = estimate.max_defect_depth_ratio
        results.append(row)

        print(f"  {image_path.name}: 정답={true_label} / 예측={estimate.guess}")

    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--complete-dir", type=str, required=True)
    parser.add_argument("--fragment-dir", type=str, required=True)
    parser.add_argument("--out-csv", type=str, default="eval_result.csv")
    args = parser.parse_args()

    detector = GroundedSAMDetector(
        text_prompt=POTTERY_TEXT_PROMPT, max_regions=1
    )

    print("[완전 폴더 평가]")
    complete_results = evaluate_folder(
        Path(args.complete_dir), "완전", detector
    )

    print("[파편 폴더 평가]")
    fragment_results = evaluate_folder(
        Path(args.fragment_dir), "파편", detector
    )

    all_results = complete_results + fragment_results

    fieldnames = sorted({key for row in all_results for key in row})
    with open(args.out_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_results)

    # 혼동행렬 (정답 x 예측)
    labels = ["완전", "파편", "판단 보류", "탐지실패", "탐지오류"]
    matrix = {t: {p: 0 for p in labels} for t in ["완전", "파편"]}

    for row in all_results:
        true_label = row["true_label"]
        pred_label = row.get("pred_label", "탐지오류")
        if pred_label not in labels:
            labels.append(pred_label)
            matrix[true_label].setdefault(pred_label, 0)
        matrix[true_label][pred_label] = matrix[true_label].get(pred_label, 0) + 1

    print("\n=== 혼동행렬 (행: 정답, 열: 예측) ===")
    header = "정답\\예측".ljust(10) + "".join(l.ljust(10) for l in labels)
    print(header)
    for true_label in ["완전", "파편"]:
        line = true_label.ljust(10) + "".join(
            str(matrix[true_label].get(l, 0)).ljust(10) for l in labels
        )
        print(line)

    n_correct = sum(
        1 for row in all_results if row.get("pred_label") == row["true_label"]
    )
    n_total = len(all_results)
    accuracy = n_correct / n_total if n_total else 0.0

    print(f"\n전체 정확도: {n_correct}/{n_total} ({accuracy:.1%})")
    print(f"결과 저장: {args.out_csv}")


if __name__ == "__main__":
    main()
