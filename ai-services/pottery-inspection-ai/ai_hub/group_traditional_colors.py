"""
group_traditional_colors.py

전통색 이름(예: 다자색, 석간주색, 두록색...)이 36개나 돼서 그대로
클래스로 쓰면 표본이 너무 쪼개진다. 한자 의미 기준으로 8개 큰
색 계열로 묶어서 클래스 수를 줄인다.

주의: 이 매핑은 한자 의미로 추론한 잠정치다. 전통색 전문 지식으로
정밀 검증한 게 아니라서, 실제 이미지 몇 장을 매핑된 계열과
대조해서 눈으로 확인하는 걸 권장한다 (예: '두록색'으로 분류된
이미지가 실제로 초록 계열이 맞는지).

사용법:
    python group_traditional_colors.py --label-dir ./pottery_subset/labels --min-count 30
"""

import argparse
import json
from collections import Counter
from pathlib import Path

# 한자 의미 기준 잠정 매핑 - 실제 이미지로 재검증 권장
COLOR_GROUP_MAP = {
    # 파랑 계열
    "청색": "파랑", "군청색": "파랑", "벽람색": "파랑", "청현색": "파랑",
    "벽청색": "파랑", "청벽색": "파랑", "흑청색": "파랑",
    # 초록 계열
    "녹색": "초록", "두록색": "초록", "하엽색": "초록", "흑록색": "초록",
    # 빨강/주황 계열
    "홍황색": "빨강・주황", "감색": "빨강・주황", "석간주색": "빨강・주황",
    "흑홍색": "빨강・주황", "연분홍색": "빨강・주황", "지황색": "빨강・주황",
    "유황색": "빨강・주황",
    # 보라 계열
    "다자색": "보라", "담자색": "보라", "자황색": "보라", "벽자색": "보라",
    "포도색": "보라",
    # 흰색 계열
    "설백색": "흰색", "유백색": "흰색", "지백색": "흰색", "소색": "흰색",
    # 검정 계열
    "흑색": "검정",
    # 회색 계열
    "회색": "회색", "연지회색": "회색",
    # 갈색 계열
    "갈색": "갈색", "토색": "갈색", "치색": "갈색", "휴색": "갈색",
    "추향색": "갈색", "구색": "갈색",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label-dir", type=str, required=True)
    parser.add_argument("--min-count", type=int, default=30)
    parser.add_argument("--out-mapping", type=str, default="color_group_result.json")
    args = parser.parse_args()

    label_dir = Path(args.label_dir)
    json_paths = sorted(label_dir.glob("*.json"))

    group_counter: Counter = Counter()
    unmapped_colors: Counter = Counter()
    mapping_result = {}

    for json_path in json_paths:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)

        images_info = data.get("images", data)
        color = images_info.get("color")

        if not color:
            continue

        group = COLOR_GROUP_MAP.get(color)
        if group is None:
            unmapped_colors[color] += 1
            group = "(매핑 없음)"

        group_counter[group] += 1
        mapping_result[json_path.name] = {"original_color": color, "color_group": group}

    print("=== 색 계열로 묶은 뒤 분포 ===")
    keep, drop = [], []
    for group, count in group_counter.most_common():
        marker = ""
        if group == "(매핑 없음)" or count < args.min_count:
            marker = f" <- 제외 후보 (기준 {args.min_count}건 미만 또는 미매핑)"
            drop.append(group)
        else:
            keep.append(group)
        print(f"  {group}: {count}건{marker}")

    print(f"\n유지할 색 계열: {keep}")
    print(f"제외 후보: {drop}")

    if unmapped_colors:
        print(f"\n[안내] 매핑표에 없는 색상명 발견 (COLOR_GROUP_MAP에 추가 필요):")
        for color, count in unmapped_colors.most_common():
            print(f"  {color}: {count}건")

    with open(args.out_mapping, "w", encoding="utf-8-sig") as f:
        json.dump(mapping_result, f, ensure_ascii=False, indent=2)
    print(f"\n파일별 매핑 결과: {args.out_mapping}")


if __name__ == "__main__":
    main()