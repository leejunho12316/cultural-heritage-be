"""
sort_by_auto_label.py

auto_label_from_name.py가 만든 auto_labeled.csv를 읽어서,
이미지 파일을 auto_label 값(파편 / 완전(추정)) 기준으로
각각의 하위 폴더에 복사한다. (검토 없이 자동 라벨을 그대로 신뢰하는
빠른 방식 - 나중에 정확도 확인이 필요해지면 needs_review 열로 걸러서
다시 확인하면 된다.)

사용법:
    python sort_by_auto_label.py --labeled auto_labeled.csv --image-dir images --out-dir sorted
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
        label = row.get("auto_label", "미분류")
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--labeled", type=str, required=True)
    parser.add_argument("--image-dir", type=str, required=True)
    parser.add_argument("--out-dir", type=str, default="./sorted")
    args = parser.parse_args()

    sort_images(args.labeled, args.image_dir, args.out_dir)
