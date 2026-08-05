"""
rank_glaze_candidates.py

glaze_rf.joblib(학습된 모델)로 판단보류 폴더 이미지들의 "무유일 확률"을
매겨서 높은 순으로 정렬한다. 무유 표본이 워낙 적어서(14건) 무작위로
더 보는 것보다, 모델이 무유라고 의심하는 것부터 확인하는 게 훨씬
효율적이다 (액티브 러닝 - 부식 프로젝트에서도 같은 아이디어를 썼다).

사용법 (heritage_corrison 루트에서):
    python rank_glaze_candidates.py --image-dir ./sorted_glaze_v2/판단보류 --model glaze_rf.joblib --out ranked_candidates.csv --top 30
"""

import argparse
import csv
import sys
from pathlib import Path

import joblib

# glaze_detector.py / evaluate_glaze.py / models/grounded_sam_detector.py는
# 저장소 재정리(v12) 이후 app/ 폴더에 있음. data_prep/에서 바로 실행해도
# 찾을 수 있도록 app/을 sys.path에 추가한다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from models.grounded_sam_detector import GroundedSAMDetector
from glaze_detector import estimate_glaze
from evaluate_glaze import imread_unicode_safe, IMAGE_EXTENSIONS, POTTERY_TEXT_PROMPT

FEATURE_COLUMNS = ["highlight_area_ratio", "highlight_kurtosis", "saturation_std"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-dir", type=str, required=True)
    parser.add_argument("--model", type=str, required=True)
    parser.add_argument("--out", type=str, default="ranked_candidates.csv")
    parser.add_argument(
        "--top", type=int, default=30, help="이 개수만큼만 우선 검토용으로 뽑음"
    )
    args = parser.parse_args()

    clf = joblib.load(args.model)
    class_index = {label: i for i, label in enumerate(clf.classes_)}
    if "무유" not in class_index:
        raise SystemExit("모델에 '무유' 클래스가 없습니다. glaze_rf.joblib이 맞는지 확인하세요.")
    unglazed_col = class_index["무유"]

    detector = GroundedSAMDetector(text_prompt=POTTERY_TEXT_PROMPT, max_regions=1)

    image_dir = Path(args.image_dir)
    image_paths = sorted(
        p for p in image_dir.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS
    )

    rows = []
    for image_path in image_paths:
        try:
            regions = detector.predict(image_path=str(image_path))
            if not regions:
                rows.append({"file": image_path.name, "unglazed_prob": None, "note": "탐지실패"})
                continue

            mask = regions[0]["mask"]
            image_bgr = imread_unicode_safe(image_path)
            if image_bgr is None:
                rows.append({"file": image_path.name, "unglazed_prob": None, "note": "이미지로드실패"})
                continue

            estimate = estimate_glaze(image_bgr, mask)
            features = [[
                estimate.highlight_area_ratio,
                estimate.highlight_kurtosis,
                estimate.saturation_std,
            ]]
            proba = clf.predict_proba(features)[0]
            unglazed_prob = float(proba[unglazed_col])

            rows.append({
                "file": image_path.name,
                "unglazed_prob": round(unglazed_prob, 4),
                "note": "",
            })
            print(f"  {image_path.name}: 무유 확률 {unglazed_prob:.1%}")

        except Exception as error:
            rows.append({"file": image_path.name, "unglazed_prob": None, "note": f"에러: {error}"})

    # 무유 확률 높은 순으로 정렬 (에러/실패는 맨 뒤로)
    rows.sort(key=lambda r: (r["unglazed_prob"] is None, -(r["unglazed_prob"] or 0)))

    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["file", "unglazed_prob", "note"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n[완료] {args.out}에 저장")
    print(f"상위 {args.top}개 (무유일 가능성이 가장 높다고 모델이 본 것) 부터 눈으로 확인하세요:")
    for row in rows[: args.top]:
        print(f"  {row['file']}: {row['unglazed_prob']}")


if __name__ == "__main__":
    main()