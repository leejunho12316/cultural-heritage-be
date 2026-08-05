"""
prepare_final_dataset.py

확정된 클래스 기준으로 pottery_subset(labels+images)을 정리해서
학습용 manifest.csv(이미지 경로 + pattern_type + era + color_group)를
만든다. 세 축 중 하나라도 제외 대상 클래스면 그 샘플은 통째로 뺀다
(멀티태스크 학습을 단순하게 하려고 - 결측 라벨을 마스킹 처리하는
방식보다 구현이 훨씬 간단하고, 여기서는 제외되는 비율이 낮아서
데이터 손실도 크지 않다).

사용법:
    python prepare_final_dataset.py --label-dir ./pottery_subset/labels --image-dir ./pottery_subset/images --out ./manifest.csv
"""

import argparse
import csv
import json
from pathlib import Path

# profile_pottery_subset.py / group_traditional_colors.py 결과로 확정된 클래스
KEEP_PATTERNS = {"식물문", "복합문", "기하문", "인공물문", "동물문", "인물문"}
KEEP_ERAS = {"삼국", "조선", "근현대", "고려", "시대미상"}

COLOR_GROUP_MAP = {
    "청색": "파랑", "군청색": "파랑", "벽람색": "파랑", "청현색": "파랑",
    "벽청색": "파랑", "청벽색": "파랑", "흑청색": "파랑",
    "녹색": "초록", "두록색": "초록", "하엽색": "초록", "흑록색": "초록",
    "홍황색": "빨강・주황", "감색": "빨강・주황", "석간주색": "빨강・주황",
    "흑홍색": "빨강・주황", "연분홍색": "빨강・주황", "지황색": "빨강・주황",
    "유황색": "빨강・주황",
    "다자색": "보라", "담자색": "보라", "자황색": "보라", "벽자색": "보라",
    "포도색": "보라",
    "설백색": "흰색", "유백색": "흰색", "지백색": "흰색", "소색": "흰색",
    "흑색": "검정",
    "회색": "회색", "연지회색": "회색",
    "갈색": "갈색", "토색": "갈색", "치색": "갈색", "휴색": "갈색",
    "추향색": "갈색", "구색": "갈색",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label-dir", type=str, required=True)
    parser.add_argument("--image-dir", type=str, required=True)
    parser.add_argument("--out", type=str, default="manifest.csv")
    args = parser.parse_args()

    label_dir = Path(args.label_dir)
    image_dir = Path(args.image_dir)

    rows = []
    stats = {"total": 0, "kept": 0, "dropped_pattern": 0, "dropped_era": 0, "dropped_color": 0, "missing_image": 0}

    for json_path in sorted(label_dir.glob("*.json")):
        stats["total"] += 1
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)

        images_info = data.get("images", data)
        pattern_type = images_info.get("pattern_type")
        era = images_info.get("era")
        color = images_info.get("color")
        object_file_name = images_info.get("object_file_name", "")

        if pattern_type not in KEEP_PATTERNS:
            stats["dropped_pattern"] += 1
            continue
        if era not in KEEP_ERAS:
            stats["dropped_era"] += 1
            continue

        color_group = COLOR_GROUP_MAP.get(color)
        if color_group is None:
            stats["dropped_color"] += 1
            continue

        candidates = [
            image_dir / f"{object_file_name}.jpg",
            image_dir / f"{object_file_name}.jpeg",
            image_dir / f"{object_file_name}.png",
        ]
        image_path = next((c for c in candidates if c.exists()), None)
        if image_path is None:
            stats["missing_image"] += 1
            continue

        rows.append({
            "image_path": str(image_path.resolve()),
            "pattern_type": pattern_type,
            "era": era,
            "color_group": color_group,
        })
        stats["kept"] += 1

    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["image_path", "pattern_type", "era", "color_group"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"[완료] {stats['kept']}/{stats['total']}건 유지 -> {args.out}")
    print(f"  제외: 문양 {stats['dropped_pattern']} / 시대 {stats['dropped_era']} / "
          f"색상 {stats['dropped_color']} / 이미지없음 {stats['missing_image']}")


if __name__ == "__main__":
    main()