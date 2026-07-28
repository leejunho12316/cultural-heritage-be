"""
emuseum_client.py

이뮤지엄(emuseum.go.kr) 오픈API 클라이언트.
문화체육관광부_국립중앙박물관_전국 박물관 유물정보 기술문서 기준으로 작성.

사용 흐름
---------
1. find_material_codes() 로 "도자기"/"토기" 관련 재질 코드부터 찾는다.
   (재질 코드가 뭔지 문서만으로는 알 수 없어서, 실제 코드 목록을
   조회해서 이름으로 검색해야 한다.)
2. find_era_codes() 로 시대 코드도 필요하면 같은 방식으로 찾는다.
3. search_relics(material_code=...) 로 유물 목록 검색 (이미지 썸네일 포함).
4. 필요하면 get_relic_detail(id) 로 상세정보 + 전체 이미지 목록 조회.
5. download_image() 로 실제 이미지 파일 저장.

사용 전에 SERVICE_KEY를 본인의 인증키로 바꿔야 한다.
"""

# str | None, dict[str, Any] 같은 문법은 파이썬 3.10+ 에서만 별도 처리
# 없이 바로 동작한다. 이 한 줄을 넣으면 어노테이션이 즉시 평가되지
# 않고 문자열로 지연 평가돼서, 3.7 이상 어디서든 TypeError 없이 돌아간다.
from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

import requests

BASE_URL = "http://www.emuseum.go.kr/openapi"

# TODO: 공공데이터포털에서 발급받은 인증키로 교체
SERVICE_KEY = "LA+VUyzSjgy2Qf85wn1HYsOD1ck5eXM/HJ89oMk7kzsQlN+KpbguvlsLijvnEG4x9R6IhjNOOR6/9grqzzMYpA=="


def _get(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    params = {"serviceKey": SERVICE_KEY, **params}
    # 기술문서에 accept 헤더로 xml/json을 선택한다고 명시되어 있음.
    # 헤더를 안 보내면 서버가 기본값(XML)으로 응답해서 response.json()이
    # 깨지는 문제가 있었다.
    headers = {"Accept": "application/json"}
    response = requests.get(
        f"{BASE_URL}/{endpoint}", params=params, headers=headers, timeout=10
    )
    response.raise_for_status()

    try:
        data = response.json()
    except ValueError as error:
        raise RuntimeError(
            "응답이 JSON이 아닙니다. "
            f"Content-Type={response.headers.get('Content-Type')}, "
            f"본문 앞부분={response.text[:300]!r}"
        ) from error

    if data.get("resultCode") != "0000":
        raise RuntimeError(
            f"API 에러 [{data.get('resultCode')}] {data.get('resultMsg')} "
            f"(요청: {endpoint} {params})"
        )

    return data


def get_code_list(parent_code: str | None = None, num_of_rows: int = 100) -> list[dict]:
    """view_code_list. parent_code가 없으면 최상위 코드들을 반환한다."""
    params = {"pageNo": 1, "numOfRows": num_of_rows}
    if parent_code:
        params["parentCode"] = parent_code

    data = _get("code", params)
    return data.get("list", [])


def find_codes_by_keyword(parent_code: str, keyword: str) -> list[dict]:
    """parent_code 하위 코드들 중 이름에 keyword가 들어간 것만 찾는다.
    예: find_codes_by_keyword('PS08', '도자')"""
    codes = get_code_list(parent_code=parent_code)
    return [c for c in codes if keyword in c.get("name", "")]


def find_material_codes(keyword: str = "도자") -> list[dict]:
    """재질(PS08) 코드 중 keyword가 포함된 것을 찾는다.
    '도자', '토기' 등 여러 키워드로 나눠 불러보는 걸 권장."""
    return find_codes_by_keyword("PS08", keyword)


def find_era_codes(nationality_parent_code: str = "PS06001", keyword: str = "") -> list[dict]:
    """국적/시대(PS06) 하위 코드 조회. 기본값은 '한국'(PS06001) 하위 시대들.
    keyword를 주면 이름으로 추가 필터링."""
    codes = get_code_list(parent_code=nationality_parent_code)
    if keyword:
        codes = [c for c in codes if keyword in c.get("name", "")]
    return codes


def build_era_code_mapping(out_csv: str = "era_code_mapping.csv") -> dict[str, str]:
    """
    PS06(국적/시대) 전체를 재귀적으로 훑어서 {코드: 이름} 매핑을 만들고
    CSV로 저장한다. nationalityCode 필드는 국적(level 3, 예: '한국')과
    시대(level 4, 예: '신라')가 같은 코드 체계 안에 섞여 있어서, 국적
    레벨 코드도 함께 저장해둔다 - metadata.csv의 nationality_code가
    국적 레벨일 수도, 시대 레벨일 수도 있기 때문이다.
    """
    import csv

    mapping: dict[str, str] = {}
    rows: list[dict[str, str]] = []

    top_level = get_code_list(parent_code="PS06")  # 국적들 (한국, 중국, 일본 ...)

    for nationality in top_level:
        nat_code = nationality["code"]
        nat_name = nationality.get("nameKr") or nationality.get("name", "")
        mapping[nat_code] = nat_name
        rows.append({"code": nat_code, "name": nat_name, "level": "국적"})

        eras = get_code_list(parent_code=nat_code)  # 그 국적 밑의 시대들
        for era in eras:
            era_code = era["code"]
            era_name = era.get("nameKr") or era.get("name", "")
            mapping[era_code] = era_name
            rows.append(
                {
                    "code": era_code,
                    "name": era_name,
                    "level": f"시대(국적:{nat_name})",
                }
            )
            time.sleep(0.2)  # API 서버 배려

        time.sleep(0.2)

    if rows:
        with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=["code", "name", "level"])
            writer.writeheader()
            writer.writerows(rows)

    print(f"[완료] 국적/시대 코드 {len(rows)}개 -> {out_csv}")
    return mapping


def apply_era_mapping(
    metadata_csv: str,
    mapping_csv: str = "era_code_mapping.csv",
    out_csv: str | None = None,
) -> None:
    """download_pottery_dataset()이 만든 metadata.csv에 시대 이름 열을 추가한다.
    out_csv를 안 주면 원본을 덮어쓰지 않고 '_with_era.csv'로 새로 저장한다
    (원본이 엑셀 등에서 열려 있어 잠겨 있어도 안전하게 동작하고,
    쓰기 도중 에러가 나도 원본 데이터가 보존된다)."""
    import csv

    with open(mapping_csv, encoding="utf-8-sig") as f:
        mapping = {row["code"]: row["name"] for row in csv.DictReader(f)}

    with open(metadata_csv, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    for row in rows:
        code = row.get("nationality_code", "")
        row["nationality_name"] = mapping.get(code, "(매핑 없음)")

    if out_csv is None:
        stem = Path(metadata_csv).stem
        out_path = str(Path(metadata_csv).with_name(f"{stem}_with_era.csv"))
    else:
        out_path = out_csv

    fieldnames = list(rows[0].keys()) if rows else []

    try:
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    except PermissionError as error:
        raise SystemExit(
            f"'{out_path}' 쓰기 실패: 다른 프로그램(엑셀 등)에서 이 파일을 "
            f"열어두신 것 같습니다. 닫고 다시 실행해주세요. (원본 오류: {error})"
        )

    unmapped = sum(1 for r in rows if r["nationality_name"] == "(매핑 없음)")
    print(f"[완료] {out_path} 에 시대 이름 추가 (매핑 안 된 항목 {unmapped}/{len(rows)}건)")


def search_relics(
    material_code: str | None = None,
    nationality_code: str | None = None,
    name: str | None = None,
    page_no: int = 1,
    num_of_rows: int = 20,
) -> dict[str, Any]:
    """view_relic_list. 재질/시대/명칭으로 유물 목록 검색."""
    params: dict[str, Any] = {"pageNo": page_no, "numOfRows": num_of_rows}
    if material_code:
        params["materialCode"] = material_code
    if nationality_code:
        params["nationalityCode"] = nationality_code
    if name:
        params["name"] = name

    return _get("relic/list", params)


def get_relic_detail(relic_id: str) -> dict[str, Any]:
    """view_relic_detail. 유물 고유 ID로 상세정보 + 전체 이미지 목록 조회."""
    return _get("relic/detail", {"id": relic_id})


def _normalize_image_url(uri: str) -> str:
    """응답의 imgUri가 'www.emuseum.go.kr/...'처럼 프로토콜 없이 오는 경우가 있어 보정."""
    if uri.startswith("http://") or uri.startswith("https://"):
        return uri
    return f"http://{uri}"


def download_image(image_uri: str, save_path: Path) -> None:
    url = _normalize_image_url(image_uri)
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_bytes(response.content)


def download_pottery_dataset(
    material_code: str,
    out_dir: str,
    nationality_code: str | None = None,
    max_items: int = 200,
    page_size: int = 20,
    request_interval_sec: float = 0.3,
) -> None:
    """material_code(+선택적으로 nationality_code)로 검색한 유물들의
    대표이미지(원본)와 메타데이터(csv)를 받는다.

    nationality_code를 안 주면 API가 주는 순서(등록순/ID순으로 추정)
    그대로 앞에서부터 받아오기 때문에, 특정 시대가 뒤쪽에 몰려있으면
    max_items 안에 아예 안 들어올 수 있다. 특정 시대(예: 삼국시대)를
    확실히 받고 싶으면 nationality_code를 지정해서 따로 받는 게 안전하다."""
    import csv

    out_path = Path(out_dir)
    image_dir = out_path / "images"
    image_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    page_no = 1
    collected = 0

    while collected < max_items:
        result = search_relics(
            material_code=material_code,
            nationality_code=nationality_code,
            page_no=page_no,
            num_of_rows=page_size,
        )
        items = result.get("list", [])
        if not items:
            break

        for item in items:
            relic_id = item.get("id", "")
            name = item.get("nameKr", "")
            nationality_code_val = item.get("nationalityCode", "")
            img_uri = item.get("imgUri", "")

            if not img_uri:
                continue

            file_name = f"{relic_id}.jpg"
            try:
                download_image(img_uri, image_dir / file_name)
            except Exception as error:
                print(f"[다운로드 실패] {relic_id}: {error}")
                continue

            rows.append(
                {
                    "id": relic_id,
                    "name": name,
                    "nationality_code": nationality_code_val,
                    "material_code": item.get("materialCode", ""),
                    "museum_name": item.get("museumName2", ""),
                    "index_word": item.get("indexWord", ""),
                    "file_name": file_name,
                }
            )
            collected += 1

            if collected >= max_items:
                break

            time.sleep(request_interval_sec)  # API 서버 배려 차원의 딜레이

        print(f"[페이지 {page_no}] 누적 {collected}건 다운로드")
        page_no += 1

    csv_path = out_path / "metadata.csv"

    # 기존 metadata.csv가 있으면(예: 다른 시대를 먼저 받아둔 경우)
    # 덮어쓰지 않고 합친다. id가 같으면 이번에 새로 받은 걸로 덮어쓴다
    # (같은 유물을 두 번 받았을 때 중복 행이 쌓이는 걸 방지).
    existing_rows_by_id: dict[str, dict] = {}
    if csv_path.exists():
        with csv_path.open(encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                existing_rows_by_id[row["id"]] = row

    new_rows_by_id = {row["id"]: row for row in rows}
    merged = {**existing_rows_by_id, **new_rows_by_id}
    merged_rows = list(merged.values())

    if merged_rows:
        with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(merged_rows[0].keys()))
            writer.writeheader()
            writer.writerows(merged_rows)

    print(
        f"[완료] 이번에 새로 받은 이미지 {collected}건, "
        f"누적 메타데이터 {len(merged_rows)}건: {csv_path}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--find-material",
        type=str,
        default=None,
        help="재질 코드 검색 키워드 (예: 도자, 토기)",
    )
    parser.add_argument(
        "--download",
        type=str,
        default=None,
        help="다운로드할 재질 코드 (find-material로 먼저 확인)",
    )
    parser.add_argument("--out-dir", type=str, default="./emuseum_pottery")
    parser.add_argument("--max-items", type=int, default=200)
    parser.add_argument(
        "--nationality-code",
        type=str,
        default=None,
        help="특정 국적/시대 코드로만 필터링 (예: 삼국시대 코드). "
        "안 주면 API가 주는 순서대로 앞에서부터 가져옴",
    )
    parser.add_argument(
        "--build-era-mapping",
        action="store_true",
        help="국적/시대 코드 전체를 era_code_mapping.csv로 저장",
    )
    parser.add_argument(
        "--apply-era-mapping",
        type=str,
        default=None,
        help="지정한 metadata.csv에 시대 이름 열을 추가 (era_code_mapping.csv가 먼저 있어야 함)",
    )
    args = parser.parse_args()

    if SERVICE_KEY == "YOUR_SERVICE_KEY_HERE":
        raise SystemExit(
            "SERVICE_KEY를 본인의 인증키로 바꾸고 다시 실행하세요."
        )

    if args.find_material:
        matches = find_material_codes(args.find_material)
        print(f"'{args.find_material}' 관련 재질 코드:")
        for m in matches:
            print(f"  {m['code']}  {m['name']}")

    if args.download:
        download_pottery_dataset(
            material_code=args.download,
            out_dir=args.out_dir,
            nationality_code=args.nationality_code,
            max_items=args.max_items,
        )

    if args.build_era_mapping:
        build_era_code_mapping()

    if args.apply_era_mapping:
        apply_era_mapping(args.apply_era_mapping)
