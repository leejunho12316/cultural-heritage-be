"""
auto_label_glaze_from_name.py

auto_label_from_name.py(완전/파편)와 같은 아이디어를 유광/무유에 적용한다.

핵심 근거 (한국 도자기 용어 자체가 유약 여부를 담고 있음)
------------------------------------------------------
- "토기"(土器): 유약 없이 비교적 저온에서 구운 것 -> 정의상 무유
- "자기"(瓷器/磁器) 계열, 즉 청자/백자/분청(사기): 유약을 발라 고온
  소성한 것 -> 정의상 유광
- 이름에 "유"(釉)가 직접 들어간 것(녹유, 갈유, 흑유, 회유, 시유 등)
  -> 확정적으로 유광
- "도기"(陶器)는 애매하다 - 녹유도기처럼 유약 있는 것도 있고 없는
  것도 있어서, "도기"만 있고 다른 신호가 없으면 사람이 봐야 한다.

이 규칙은 (완전/파편 키워드와 달리) 아직 실측 데이터로 검증하지
않았다. 처음 30~50건 정도는 결과를 눈으로 확인해서 정말 믿을 만한지
확인하고 쓰는 걸 권장한다.

사용법:
    python auto_label_glaze_from_name.py --metadata metadata_with_era.csv --out auto_labeled_glaze.csv
"""

import argparse
import csv

# 확정적 유광 신호 (유약을 뜻하는 한자/단어가 이름에 직접 있음)
GLAZE_EXPLICIT_KEYWORDS = [
    "釉", "유약", "시유", "유병",
    "녹유", "갈유", "흑유", "회유", "청유", "재유", "회유도기",
]

# 자기 계열 - 정의상 유광
PORCELAIN_KEYWORDS = ["청자", "백자", "분청", "磁器", "瓷器"]

# 토기 - 정의상 무유 (단, 위 유광 키워드가 같이 있으면 그쪽이 우선)
EARTHENWARE_KEYWORDS = ["토기", "土器"]

# 애매함 - 사람 확인 필요
AMBIGUOUS_KEYWORDS = ["도기", "陶器"]


def classify_from_text(name: str, index_word: str = "") -> tuple[str, bool, str]:
    """반환: (auto_label, needs_review, matched_rule)"""
    combined = f"{name} {index_word}"

    for keyword in GLAZE_EXPLICIT_KEYWORDS:
        if keyword in combined:
            return "유광", False, f"유약직접명시:{keyword}"

    for keyword in PORCELAIN_KEYWORDS:
        if keyword in combined:
            return "유광", False, f"자기계열:{keyword}"

    for keyword in EARTHENWARE_KEYWORDS:
        if keyword in combined:
            return "무유", False, f"토기:{keyword}"

    for keyword in AMBIGUOUS_KEYWORDS:
        if keyword in combined:
            return "판단보류", True, f"도기(애매):{keyword}"

    return "판단보류", True, "규칙 매칭 없음"


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
        row["auto_label_glaze"] = auto_label
        row["needs_review_glaze"] = needs_review
        row["matched_rule"] = matched

    out_path = args.out or args.metadata.replace(".csv", "_auto_labeled_glaze.csv")
    fieldnames = list(rows[0].keys()) if rows else []
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    n_glazed = sum(1 for r in rows if r["auto_label_glaze"] == "유광")
    n_unglazed = sum(1 for r in rows if r["auto_label_glaze"] == "무유")
    n_review = sum(1 for r in rows if r["needs_review_glaze"])

    print(f"[완료] 총 {len(rows)}건")
    print(f"  자동 분류(유광, 검토 불필요): {n_glazed}건")
    print(f"  자동 분류(무유, 검토 불필요): {n_unglazed}건")
    print(f"  사람 검토 필요(도기/매칭없음): {n_review}건")
    print(f"  -> {out_path}")


if __name__ == "__main__":
    main()