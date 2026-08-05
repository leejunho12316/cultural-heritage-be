"""
filter_grayscale_from_manifest.py

check_grayscale_images.py의 결과(grayscale_check_result.csv)를 이용해
흑백/세피아 의심 이미지를 제거한 새 manifest를 만든다. 색상 분류
성능이 흑백 사진 오염 때문에 낮았다는 게 확인됐으니, 이 이미지들을
빼고 문양/시대/색상을 다시 학습해야 한다.

사용법:
    python filter_grayscale_from_manifest.py \
        --manifest manifest_train.csv \
        --grayscale-result grayscale_check_result.csv \
        --out manifest_train_clean.csv
"""

import argparse

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=str, required=True)
    parser.add_argument("--grayscale-result", type=str, required=True)
    parser.add_argument("--out", type=str, required=True)
    args = parser.parse_args()

    manifest_df = pd.read_csv(args.manifest)
    grayscale_df = pd.read_csv(args.grayscale_result)

    grayscale_suspect_paths = set(
        grayscale_df.loc[grayscale_df["is_grayscale_suspect"], "image_path"]
    )

    before = len(manifest_df)
    clean_df = manifest_df[~manifest_df["image_path"].isin(grayscale_suspect_paths)]
    after = len(clean_df)

    clean_df.to_csv(args.out, index=False, encoding="utf-8-sig")

    print(f"[완료] {before}건 -> {after}건 ({before - after}건 제거, {(before - after) / before:.1%})")
    print(f"저장: {args.out}")

    print("\n[남은 데이터의 color_group 분포]")
    print(clean_df["color_group"].value_counts())


if __name__ == "__main__":
    main()