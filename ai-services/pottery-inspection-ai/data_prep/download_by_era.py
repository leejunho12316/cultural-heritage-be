"""
download_by_era.py

era_code_mapping.csv 보면서 확인한 시대 코드들을 아래 ERA_CODES에
채워 넣고 실행하면, 시대별로 지정한 개수(기본 100개)씩 순서대로
받는다. emuseum_client.py의 download_pottery_dataset()은 같은
out_dir에 여러 번 받아도 metadata.csv가 누적되도록 이미 고쳐놨으니,
전부 같은 폴더로 받아도 안전하다.

사용법:
    1. 아래 ERA_CODES 딕셔너리에 {"보기 좋은 이름": "코드"} 형태로 채운다.
    2. python download_by_era.py --material PS08003 --out-dir ./emuseum_pottery_all --items-per-era 100
"""

import argparse
import time

from emuseum_client import SERVICE_KEY, download_pottery_dataset

# era_code_mapping.csv에서 직접 확인한 코드로 채우세요.
# 예시로 넣어둔 값은 실제 코드가 아닐 수 있으니 반드시 본인이 받은
# era_code_mapping.csv 기준으로 바꿔서 쓰세요.
ERA_CODES: dict[str, str] = {

    "고려" : "PS06001016"
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--material", type=str, default="PS08003")
    parser.add_argument("--out-dir", type=str, default="./emuseum_pottery_all")
    parser.add_argument("--items-per-era", type=int, default=100)
    parser.add_argument(
        "--era-interval-sec",
        type=float,
        default=2.0,
        help="시대별 다운로드 사이 대기시간 (API 서버 배려 + 500 에러 예방)",
    )
    args = parser.parse_args()

    if SERVICE_KEY == "YOUR_SERVICE_KEY_HERE":
        raise SystemExit(
            "emuseum_client.py의 SERVICE_KEY를 먼저 본인 인증키로 바꾸세요."
        )

    print(f"총 {len(ERA_CODES)}개 시대, 시대당 {args.items_per_era}건씩 받습니다.")
    print(f"저장 위치(공용): {args.out_dir}\n")

    for era_name, era_code in ERA_CODES.items():
        print(f"===== [{era_name}] (코드: {era_code}) =====")
        try:
            download_pottery_dataset(
                material_code=args.material,
                out_dir=args.out_dir,
                nationality_code=era_code,
                max_items=args.items_per_era,
            )
        except Exception as error:
            print(f"[{era_name}] 다운로드 중 에러 발생, 다음 시대로 넘어갑니다: {error}")

        print(f"[{era_name}] 완료, {args.era_interval_sec}초 대기 후 다음 시대\n")
        time.sleep(args.era_interval_sec)

    print("전체 시대 다운로드 완료.")
    print(f"metadata.csv는 {args.out_dir}에 시대 구분 없이 누적 저장되어 있습니다.")
    print("시대별로 몇 건씩 받혔는지 확인하려면 metadata.csv의 nationality_code 열을 보세요.")


if __name__ == "__main__":
    main()