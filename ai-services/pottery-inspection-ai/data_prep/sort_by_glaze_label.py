"""
sort_by_glaze_label.py

auto_label_glaze_from_name.py가 만든 auto_labeled_glaze.csv를 읽어서,
이미지를 auto_label_glaze 값(유광 / 무유 / 판단보류) 기준으로
각각의 하위 폴더에 복사한다. sort_by_auto_label.py(완전/파편용)와
같은 패턴이다.

사람이 눈으로 봐야 하는 건 '판단보류' 폴더뿐이다 - 유광/무유는
이름 규칙으로 확정된 것들이라 그대로 정답으로 써도 된다.

사용법:
    python sort_by_glaze_label.py --labeled auto_labeled_glaze.csv --image-dir images --out-dir sorted_glaze
"""

import argparse
import csv
import shutil
from pathlib import Path


def sort_images(labeled_csv: str, image_dir: str, out_dir: str) -> None:
    image_dir_path = Path(image_dir)
    out_dir_path = Path(out_dir)

    with open(labeled_csv, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    counts: dict[str, int] = {}
    missing = 0

    for row in rows:
        label = row.get("auto_label_glaze", "미분류")
        file_name = row.get("file_name", "")

        src = image_dir_path / file_name
        if not src.exists():
            missing += 1
            continue

        dest_dir = out_dir_path / label
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest_dir / file_name)

        counts[label] = counts.get(label, 0) + 1

    print("[완료] 라벨별 이미지 수:")
    for label, count in counts.items():
        print(f"  {label}: {count}장")
    if missing:
        print(f"  (이미지 파일을 못 찾은 행 {missing}건 - image_dir 경로 확인 필요)")
    print(f"저장 위치: {out_dir_path.resolve()}")
    print(
        "\n'판단보류' 폴더만 눈으로 확인해서 '유광'/'무유' 폴더로 다시 "
        "옮겨주시면, evaluate_glaze.py에는 '유광'/'무유' 두 폴더만 넣으면 됩니다."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--labeled", type=str, required=True)
    parser.add_argument("--image-dir", type=str, required=True)
    parser.add_argument("--out-dir", type=str, default="./sorted_glaze")
    args = parser.parse_args()

    sort_images(args.labeled, args.image_dir, args.out_dir)