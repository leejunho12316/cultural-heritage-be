"""
scan_ai_hub_pattern_dataset.py

AI Hub "한국 전통 문양 이미지 생성을 위한 초거대 AI 학습용 데이터"의
JSON 라벨들을 스캔한다.

1단계: material 필드 전체 고유값과 개수를 보여준다 (도자기 관련이
       정확히 뭐라고 적혀있는지 모르니 먼저 확인해야 함).
2단계: --filter-keywords로 필터링해서 도자기 서브셋을 만든다
       (원본은 안 건드리고, 이미지+JSON을 별도 폴더로 복사).

json 구조 예시 (제공받은 문서 기준):
    {
      "images": {
        "object_file_name": "...",
        "material": "토제",
        "era": "고려",
        "pattern_type": "인공물문",
        "color": "다자색",
        ...
      },
      ...
    }

사용법:
    # 1단계: 재질 분포부터 확인
    python scan_ai_hub_pattern_dataset.py --json-dir ./labels --mode scan

    # 2단계: 도자기 관련 재질로 필터링 (1단계 결과 보고 키워드 확정한 뒤)
    python scan_ai_hub_pattern_dataset.py --json-dir ./labels --image-dir ./images \
        --mode filter --filter-keywords 토제 자기 도자기 청자 백자 분청 \
        --out-dir ./pottery_subset
"""

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path


def load_all_labels(json_dir: Path) -> list[dict]:
    labels = []
    for json_path in sorted(json_dir.glob("*.json")):
        try:
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            print(f"[읽기 실패] {json_path.name}: {error}")
            continue

        images_info = data.get("images", data)  # 혹시 최상위에 바로 있는 경우도 방어
        images_info["_json_path"] = json_path
        labels.append(images_info)

    return labels


def scan_materials(labels: list[dict]) -> None:
    material_counter = Counter(item.get("material", "(없음)") for item in labels)
    era_counter = Counter(item.get("era", "(없음)") for item in labels)
    pattern_counter = Counter(item.get("pattern_type", "(없음)") for item in labels)

    print(f"총 라벨 수: {len(labels)}건\n")

    print("=== material(재질) 분포 ===")
    for material, count in material_counter.most_common():
        print(f"  {material}: {count}건")

    print("\n=== era(시대) 분포 ===")
    for era, count in era_counter.most_common():
        print(f"  {era}: {count}건")

    print("\n=== pattern_type(문양 형태) 분포 ===")
    for pattern, count in pattern_counter.most_common():
        print(f"  {pattern}: {count}건")


def filter_and_copy(
    labels: list[dict],
    image_dir: Path,
    out_dir: Path,
    keywords: list[str],
) -> None:
    out_image_dir = out_dir / "images"
    out_label_dir = out_dir / "labels"
    out_image_dir.mkdir(parents=True, exist_ok=True)
    out_label_dir.mkdir(parents=True, exist_ok=True)

    matched = [
        item
        for item in labels
        if any(keyword in item.get("material", "") for keyword in keywords)
    ]

    print(f"필터 키워드 {keywords} 매칭: {len(matched)}건 / 전체 {len(labels)}건")

    copied = 0
    missing_images = 0

    for item in matched:
        object_file_name = item.get("object_file_name", "")
        candidates = [
            image_dir / f"{object_file_name}.jpg",
            image_dir / f"{object_file_name}.jpeg",
            image_dir / f"{object_file_name}.png",
        ]
        src_image = next((c for c in candidates if c.exists()), None)

        if src_image is None:
            missing_images += 1
            continue

        shutil.copy2(src_image, out_image_dir / src_image.name)

        src_json = item["_json_path"]
        shutil.copy2(src_json, out_label_dir / src_json.name)

        copied += 1

    print(f"[완료] 이미지+라벨 {copied}건 복사")
    if missing_images:
        print(f"  (이미지 파일을 못 찾은 라벨 {missing_images}건 - image_dir 경로/확장자 확인 필요)")
    print(f"저장 위치: {out_dir.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-dir", type=str, required=True)
    parser.add_argument("--image-dir", type=str, default=None)
    parser.add_argument("--mode", choices=["scan", "filter"], default="scan")
    parser.add_argument("--filter-keywords", nargs="+", default=[])
    parser.add_argument("--out-dir", type=str, default="./pottery_subset")
    args = parser.parse_args()

    json_dir = Path(args.json_dir)
    labels = load_all_labels(json_dir)

    if not labels:
        raise SystemExit(f"{json_dir}에서 JSON 라벨을 하나도 못 읽었습니다. 경로를 확인하세요.")

    if args.mode == "scan":
        scan_materials(labels)
    else:
        if not args.image_dir:
            raise SystemExit("--mode filter 사용시 --image-dir이 필요합니다.")
        if not args.filter_keywords:
            raise SystemExit("--filter-keywords를 하나 이상 지정하세요 (예: 토제 자기 도자기)")

        filter_and_copy(
            labels,
            Path(args.image_dir),
            Path(args.out_dir),
            args.filter_keywords,
        )


if __name__ == "__main__":
    main()