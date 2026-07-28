"""
check_grayscale_images.py

manifest.csv(image_path, ..., color_group)의 이미지들을 훑어서
채도(HSV S 채널 평균)가 낮은 것들을 "흑백/세피아 의심"으로 표시한다.
색상 분류기 정확도가 낮게 나온 원인이 "애초에 색이 없는 사진이
섞여서"인지 확인하기 위한 진단 스크립트.

판단 기준: 이미지 전체 픽셀의 평균 채도가 low-saturation-threshold
미만이면 흑백/세피아로 간주 (세피아는 색조가 있어도 채도 자체는
낮은 경우가 많아 같이 잡힌다).

사용법:
    python check_grayscale_images.py --manifest manifest_train.csv --threshold 25
"""

import argparse
from collections import defaultdict

import cv2
import numpy as np
import pandas as pd


def imread_unicode_safe(path: str):
    data = np.fromfile(path, dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def mean_saturation(image_bgr: np.ndarray) -> float:
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    return float(hsv[..., 1].mean())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=str, required=True)
    parser.add_argument(
        "--threshold",
        type=float,
        default=25.0,
        help="평균 채도(0~255)가 이 값 미만이면 흑백/세피아 의심",
    )
    parser.add_argument("--out", type=str, default="grayscale_check_result.csv")
    args = parser.parse_args()

    df = pd.read_csv(args.manifest)
    print(f"총 {len(df)}건 확인 중...")

    results = []
    by_color_group = defaultdict(lambda: {"total": 0, "grayscale": 0})

    for i, row in df.iterrows():
        image = imread_unicode_safe(row["image_path"])
        if image is None:
            continue

        sat = mean_saturation(image)
        is_grayscale = sat < args.threshold

        results.append({
            "image_path": row["image_path"],
            "color_group": row["color_group"],
            "mean_saturation": round(sat, 2),
            "is_grayscale_suspect": is_grayscale,
        })

        by_color_group[row["color_group"]]["total"] += 1
        if is_grayscale:
            by_color_group[row["color_group"]]["grayscale"] += 1

        if (i + 1) % 200 == 0 or (i + 1) == len(df):
            print(f"  {i + 1}/{len(df)}")

    result_df = pd.DataFrame(results)
    result_df.to_csv(args.out, index=False, encoding="utf-8-sig")

    total = len(result_df)
    grayscale_count = int(result_df["is_grayscale_suspect"].sum())

    print(f"\n[전체] 흑백/세피아 의심: {grayscale_count}/{total} ({grayscale_count/total:.1%})")

    print("\n[색상 계열별 흑백 비율] - 특정 계열에 쏠려있으면 그 계열이 학습을 망칠 가능성이 큼")
    for group, stats in sorted(by_color_group.items(), key=lambda x: -x[1]["grayscale"] / x[1]["total"]):
        ratio = stats["grayscale"] / stats["total"]
        print(f"  {group}: {stats['grayscale']}/{stats['total']} ({ratio:.1%})")

    print(f"\n결과 저장: {args.out}")

    if grayscale_count / total > 0.15:
        print(
            "\n[결론] 흑백/세피아 사진이 15% 넘게 섞여 있습니다. "
            "이게 색상 분류기 성능(32%)이 낮았던 원인일 가능성이 높습니다. "
            "이 사진들을 제외하고 재학습해보는 걸 추천합니다."
        )
    else:
        print(
            "\n[결론] 흑백/세피아 비율이 낮습니다. 색상 성능 저하의 원인은 "
            "다른 곳(색상 그룹 매핑 오류, 색상 인식이 실제로 어려운 태스크 등)일 "
            "가능성이 더 큽니다."
        )


if __name__ == "__main__":
    main()