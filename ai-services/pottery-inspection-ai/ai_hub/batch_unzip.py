"""
batch_unzip.py

AI Hub 데이터가 문양유형(SM/DM/IM/IG/JY/MJ/GH/BH) x 용도(SH/KC/MH)별로
쪼개진 zip 수십 개로 와서, 한 번에 다 풀어주는 스크립트.

같은 폴더(01.원천데이터 또는 02.라벨링데이터) 안의 zip을 전부
out-dir 하나로 모아서 푼다 - 각 zip 안에 파일명이 안 겹치는 걸
전제로 한다 (object_file_name/relic_no가 고유하다면 안전).

사용법:
    python batch_unzip.py --zip-dir "Validation/01.원천데이터" --out-dir "Validation_images"
    python batch_unzip.py --zip-dir "Validation/02.라벨링데이터" --out-dir "Validation_labels"
"""

import argparse
import zipfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip-dir", type=str, required=True)
    parser.add_argument("--out-dir", type=str, required=True)
    args = parser.parse_args()

    zip_dir = Path(args.zip_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    zip_paths = sorted(zip_dir.glob("*.zip"))
    if not zip_paths:
        raise SystemExit(f"{zip_dir}에 zip 파일이 없습니다. 경로를 확인하세요.")

    print(f"총 {len(zip_paths)}개 zip 발견, {out_dir}로 압축 해제합니다.")

    total_extracted = 0
    for zip_path in zip_paths:
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(out_dir)
                n_files = len(zf.namelist())
        except zipfile.BadZipFile as error:
            print(f"[실패] {zip_path.name}: {error}")
            continue

        total_extracted += n_files
        print(f"  {zip_path.name}: {n_files}개 파일")

    print(f"\n[완료] 총 {total_extracted}개 파일 압축 해제됨 -> {out_dir.resolve()}")


if __name__ == "__main__":
    main()