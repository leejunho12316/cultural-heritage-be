"""
profile_pottery_subset.py

scan_ai_hub_pattern_dataset.py의 filter 모드로 걸러낸 도자기
서브셋(pottery_subset/labels) 안에서 pattern_type/era/color 분포를
확인한다. 전체 4908건 기준 분포와 도자기만 걸렀을 때 분포는 다를
수 있어서, 클래스를 뺄지 말지는 이 결과로 정해야 한다.

min-count 기준 이하인 클래스는 "제외 후보"로 따로 보여준다.

사용법:
    python profile_pottery_subset.py --label-dir ./pottery_subset/labels --min-count 30
"""

import argparse
import json
from collections import Counter
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label-dir", type=str, required=True)
    parser.add_argument(
        "--min-count",
        type=int,
        default=30,
        help="이 개수 미만인 클래스는 '제외 후보'로 표시",
    )
    args = parser.parse_args()

    label_dir = Path(args.label_dir)
    json_paths = sorted(label_dir.glob("*.json"))

    if not json_paths:
        raise SystemExit(f"{label_dir}에 JSON이 없습니다.")

    pattern_counter: Counter = Counter()
    era_counter: Counter = Counter()
    color_counter: Counter = Counter()
    schema1_count = 0  # material/era/pattern_type이 있는 스키마
    schema2_count = 0  # 캡션 전용 스키마 (필드 없음)

    for json_path in json_paths:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)

        images_info = data.get("images", data)

        pattern_type = images_info.get("pattern_type")
        era = images_info.get("era")
        color = images_info.get("color")

        if pattern_type is None and era is None:
            schema2_count += 1
            continue

        schema1_count += 1
        pattern_counter[pattern_type or "(없음)"] += 1
        era_counter[era or "(없음)"] += 1
        color_counter[color or "(없음)"] += 1

    print(f"총 라벨: {len(json_paths)}건 (필드 있는 스키마 {schema1_count}건 / 캡션 전용 {schema2_count}건)")
    print(f"\n[안내] 멀티태스크 학습에는 필드 있는 스키마 {schema1_count}건만 쓸 수 있습니다.\n")

    for name, counter in [("pattern_type(문양)", pattern_counter), ("era(시대)", era_counter), ("color(색상)", color_counter)]:
        print(f"=== {name} 분포 ===")
        keep, drop = [], []
        for label, count in counter.most_common():
            marker = ""
            if label == "(없음)":
                marker = " <- 결측치, 항상 제외"
                drop.append(label)
            elif count < args.min_count:
                marker = f" <- {args.min_count}건 미만, 제외 후보"
                drop.append(label)
            else:
                keep.append(label)
            print(f"  {label}: {count}건{marker}")

        print(f"  유지: {keep}")
        print(f"  제외 후보: {drop}\n")


if __name__ == "__main__":
    main()