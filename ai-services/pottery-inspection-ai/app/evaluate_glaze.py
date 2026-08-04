"""
evaluate_glaze.py

사람이 눈으로 분류해둔 유광/무유 폴더를 정답(ground truth)으로 놓고,
Grounding DINO + SAM2로 마스크를 뽑은 뒤 glaze_detector.py의
estimate_glaze()가 실제로 얼마나 맞히는지 평가한다.
evaluate_completeness.py와 완전히 같은 패턴이다.

사용법 (heritage_corrison 루트에서, glaze_detector.py도 같은 폴더에 있어야 함):
    python evaluate_glaze.py \
        --glazed-dir ./유광 \
        --unglazed-dir ./무유 \
        --out-csv ./eval_glaze_result.csv
"""

import argparse
import csv
from pathlib import Path

import cv2
import numpy as np

from models.grounded_sam_detector import GroundedSAMDetector
from glaze_detector import estimate_glaze

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

POTTERY_TEXT_PROMPT = (
    "ceramic vessel. pottery. clay pot. "
    "ceramic fragment. broken pottery shard."
)


def imread_unicode_safe(path: Path):
    """cv2.imread()는 Windows에서 한글 등 비ASCII 경로를 못 읽는
    버그가 있다 (내부적으로 시스템 로케일 기반 fopen을 쓰기 때문).
    파일을 바이트로 직접 읽어서 cv2.imdecode로 디코딩하면 경로
    인코딩과 무관하게 항상 안전하게 동작한다."""
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


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

        image_bgr = imread_unicode_safe(image_path)
        if image_bgr is None:
            row["pred_label"] = "이미지로드실패"
            results.append(row)
            continue

        estimate = estimate_glaze(image_bgr, mask)

        row["pred_label"] = "유광" if estimate.has_glaze_guess else "무유"
        row["confidence_label"] = estimate.confidence_label
        row["highlight_area_ratio"] = estimate.highlight_area_ratio
        row["highlight_kurtosis"] = estimate.highlight_kurtosis
        row["saturation_std"] = estimate.saturation_std
        results.append(row)

        print(
            f"  {image_path.name}: 정답={true_label} / "
            f"예측={row['pred_label']} ({estimate.confidence_label})"
        )

    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--glazed-dir", type=str, required=True)
    parser.add_argument("--unglazed-dir", type=str, required=True)
    parser.add_argument("--out-csv", type=str, default="eval_glaze_result.csv")
    args = parser.parse_args()

    detector = GroundedSAMDetector(
        text_prompt=POTTERY_TEXT_PROMPT, max_regions=1
    )

    print("[유광 폴더 평가]")
    glazed_results = evaluate_folder(Path(args.glazed_dir), "유광", detector)

    print("[무유 폴더 평가]")
    unglazed_results = evaluate_folder(Path(args.unglazed_dir), "무유", detector)

    all_results = glazed_results + unglazed_results

    fieldnames = sorted({key for row in all_results for key in row})
    with open(args.out_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_results)

    labels = ["유광", "무유", "탐지실패", "탐지오류", "이미지로드실패"]
    matrix = {t: {p: 0 for p in labels} for t in ["유광", "무유"]}

    for row in all_results:
        true_label = row["true_label"]
        pred_label = row.get("pred_label", "탐지오류")
        matrix[true_label][pred_label] = matrix[true_label].get(pred_label, 0) + 1

    print("\n=== 혼동행렬 (행: 정답, 열: 예측) ===")
    header = "정답\\예측".ljust(10) + "".join(l.ljust(10) for l in labels)
    print(header)
    for true_label in ["유광", "무유"]:
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
