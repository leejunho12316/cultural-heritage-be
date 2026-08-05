"""
auto_label_from_name.py

metadata.csv의 유물명(name)/색인어(index_word) 텍스트에서 "편", "片",
"조각", "파편" 같은 키워드로 완전/파편을 1차 자동 분류한다.

왜 이게 통할 가능성이 높은가
---------------------------
이뮤지엄 유물명은 "靑磁片", "백자저부편"처럼 발굴/소장 관행상
파편이면 이름 자체에 "편"/"片"을 붙이는 경우가 매우 흔하다.
24장 수동 검토 결과, 이 키워드가 있는 항목은 전부 실제로 파편이었다
(오탐 0건) - 즉 파편 판별에는 이 규칙이 꽤 신뢰할 만하다.

반대로 "완전"은 이 규칙으로 확신할 수 없다 - 이름에 파편 키워드가
없어도 실제로는 접합/복원된 유물일 수 있다 (24장 중 3장이
"복원됨 의심"이었는데, 전부 이름만으로는 구분이 안 됐다).

그래서 이 스크립트는:
- 키워드로 파편이 확실한 것 -> auto_label="파편", needs_review=False
- 나머지 -> auto_label="완전(추정)", needs_review=True
  (완전 vs 복원됨을 텍스트만으로 구분 못하므로 전부 검토 대상으로 남긴다)

사람이 볼 이미지 수를 줄이는 게 목적이라면, needs_review=True인
것들만 훑어봐도 파편 400장을 전부 보는 것보다 훨씬 적을 가능성이 높다.

사용법:
    python auto_label_from_name.py --metadata metadata_with_era.csv --out auto_labeled.csv
"""

import argparse
import csv
import re

FRAGMENT_KEYWORDS = ["편", "片", "조각", "파편"]

# 오탐 방지: "편"이 들어가지만 파편이 아닌 유물명도 있을 수 있어서
# (예: 사람 이름, 지명 등에 우연히 "편"이 들어가는 경우) 필요하면
# 여기에 예외 키워드를 추가한다. 지금은 24장 검토에서 그런 케이스가
# 없었지만, 400장으로 늘리면 나올 수 있으니 확인 후 채워 넣을 것.
FALSE_POSITIVE_EXCEPTIONS: list[str] = []


def classify_from_text(name: str, index_word: str) -> tuple[str, bool, str]:
    """반환: (auto_label, needs_review, matched_keyword)"""
    combined = f"{name} {index_word}"

    for exception in FALSE_POSITIVE_EXCEPTIONS:
        if exception in combined:
            return "완전(추정)", True, f"예외처리:{exception}"

    for keyword in FRAGMENT_KEYWORDS:
        if keyword in combined:
            return "파편", False, keyword

    return "완전(추정)", True, ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", type=str, required=True)
    parser.add_argument("--out", type=str, default=None)
    args = parser.parse_args()

    with open(args.metadata, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    for row in rows:
        auto_label, needs_review, matched = classify_from_text(
            row.get("name", ""), row.get("index_word", "")
        )
        row["auto_label"] = auto_label
        row["needs_review"] = needs_review
        row["matched_keyword"] = matched

    out_path = args.out or args.metadata.replace(".csv", "_auto_labeled.csv")
    fieldnames = list(rows[0].keys()) if rows else []
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    n_fragment = sum(1 for r in rows if r["auto_label"] == "파편")
    n_review = sum(1 for r in rows if r["needs_review"])

    print(f"[완료] 총 {len(rows)}건")
    print(f"  자동 분류(파편, 검토 불필요): {n_fragment}건")
    print(f"  사람 검토 필요(완전/복원됨 구분 안 됨): {n_review}건")
    print(f"  -> {out_path}")


if __name__ == "__main__":
    main()
