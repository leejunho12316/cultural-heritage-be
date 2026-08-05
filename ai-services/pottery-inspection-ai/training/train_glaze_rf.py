"""
train_glaze_rf.py

evaluate_glaze.py 규칙 기반 결과: 전체 정확도 81.5%였지만 실제로는
무유 재현율 0% (무유 14건 중 하나도 못 맞힘) - 그냥 "전부 유광"이라고
찍은 것과 다르지 않은 상태였다. train_completeness_rf.py와 같은
패턴으로 eval_glaze_result.csv의 특징값(highlight_area_ratio,
highlight_kurtosis, saturation_std)으로 Random Forest를 학습한다.

주의: 무유 표본이 워낙 적어서(9~14건) 교차검증 자체가 불안정할 수
있다. class_weight="balanced"로 소수 클래스를 무시하지 않게는
했지만, 표본이 너무 적으면 이 결과도 크게 신뢰하기는 어렵다 -
결과를 보고 "무유 표본을 더 확보해야 한다"는 신호로 받아들이는 게
정확할 수 있다.

사용법:
    python train_glaze_rf.py --eval-csv eval_glaze_result.csv
"""

import argparse
import csv

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import classification_report, confusion_matrix

FEATURE_COLUMNS = ["highlight_area_ratio", "highlight_kurtosis", "saturation_std"]


def load_dataset(eval_csv: str) -> tuple[np.ndarray, np.ndarray, list[str]]:
    with open(eval_csv, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    usable_rows = [
        row
        for row in rows
        if all(row.get(col) not in (None, "") for col in FEATURE_COLUMNS)
    ]

    excluded = len(rows) - len(usable_rows)
    if excluded:
        print(f"[안내] 특징값 없는 행(탐지실패/오류) {excluded}건은 학습에서 제외")

    X = np.array(
        [[float(row[col]) for col in FEATURE_COLUMNS] for row in usable_rows]
    )
    y = np.array([row["true_label"] for row in usable_rows])
    file_names = [row["file"] for row in usable_rows]

    return X, y, file_names


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-csv", type=str, required=True)
    parser.add_argument("--model-out", type=str, default="glaze_rf.joblib")
    parser.add_argument("--n-splits", type=int, default=5)
    args = parser.parse_args()

    X, y, file_names = load_dataset(args.eval_csv)
    n_glazed = int((y == "유광").sum())
    n_unglazed = int((y == "무유").sum())
    print(f"학습 데이터: {len(y)}건 (유광 {n_glazed}건 / 무유 {n_unglazed}건)")

    if len(set(y)) < 2:
        raise SystemExit("정답 레이블이 한 종류뿐입니다 (유광/무유 둘 다 있어야 학습 가능).")

    if min(n_glazed, n_unglazed) < 10:
        print(
            "[경고] 소수 클래스(무유) 표본이 10건 미만입니다. "
            "아래 결과는 참고용일 뿐, 표본을 더 늘린 뒤 다시 확인하는 걸 권장합니다."
        )

    clf = RandomForestClassifier(
        n_estimators=300,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )

    class_counts = {label: int((y == label).sum()) for label in set(y)}
    smallest_class_size = min(class_counts.values())
    n_splits = min(args.n_splits, smallest_class_size)
    if n_splits < 2:
        raise SystemExit(
            f"무유 표본이 {smallest_class_size}건뿐이라 교차검증 자체가 불가능합니다 "
            "(최소 2건 이상 필요). 무유 표본을 더 확보해주세요."
        )
    if n_splits < args.n_splits:
        print(f"[안내] 소수 클래스({smallest_class_size}건) 때문에 n_splits을 {n_splits}로 낮춤")

    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
    y_pred = cross_val_predict(clf, X, y, cv=skf)

    print("\n=== 분류 리포트 (교차검증) ===")
    print(classification_report(y, y_pred))

    print("=== 혼동행렬 (행: 정답, 열: 예측) ===")
    labels = sorted(set(y))
    matrix = confusion_matrix(y, y_pred, labels=labels)
    header = "정답\\예측".ljust(10) + "".join(l.ljust(10) for l in labels)
    print(header)
    for label, row in zip(labels, matrix):
        print(label.ljust(10) + "".join(str(v).ljust(10) for v in row))

    accuracy = float((y_pred == y).mean())
    print(f"\n전체 정확도(교차검증): {accuracy:.1%}")
    print("(규칙 기반 evaluate_glaze.py 결과: 81.5%였지만 무유 재현율 0%였던 것과 비교)")

    clf.fit(X, y)
    importances = dict(zip(FEATURE_COLUMNS, clf.feature_importances_))
    print("\n=== 특징 중요도 ===")
    for feature, importance in sorted(importances.items(), key=lambda item: -item[1]):
        print(f"  {feature}: {importance:.3f}")

    joblib.dump(clf, args.model_out)
    print(f"\n모델 저장: {args.model_out}")


if __name__ == "__main__":
    main()