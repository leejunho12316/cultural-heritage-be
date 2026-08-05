"""
train_completeness_rf.py

규칙 기반(if-else 임계값) 완전/파편 판별이 실측 정확도 49%로 사실상
동전 던지기 수준이었다 (특히 파편 재현율 5.2%). 임계값을 손으로 더
조정하는 대신, evaluate_completeness.py가 만든 eval_result.csv의
형태 특징(symmetry_iou, circularity, max_defect_depth_ratio)으로
Random Forest를 학습시킨다.

부식 프로젝트에서 썼던 것과 같은 패턴이다:
K-means/규칙 기반 = 후보/특징 생성, Random Forest = 실제 분류.

사용법:
    python train_completeness_rf.py --eval-csv eval_result.csv
"""

import argparse
import csv

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import classification_report, confusion_matrix

FEATURE_COLUMNS = ["symmetry_iou", "circularity", "max_defect_depth_ratio"]


def load_dataset(eval_csv: str) -> tuple[np.ndarray, np.ndarray, list[str]]:
    with open(eval_csv, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    # 탐지실패/탐지오류로 특징값 자체가 없는 행은 학습에서 제외한다
    # (분류기가 배울 신호가 없는 행이라 넣으면 노이즈만 된다).
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
    parser.add_argument(
        "--model-out", type=str, default="completeness_rf.joblib"
    )
    parser.add_argument("--n-splits", type=int, default=5)
    args = parser.parse_args()

    X, y, file_names = load_dataset(args.eval_csv)
    print(f"학습 데이터: {len(y)}건 (완전 {sum(y=='완전')}건 / 파편 {sum(y=='파편')}건)")

    if len(set(y)) < 2:
        raise SystemExit(
            "정답 레이블이 한 종류뿐입니다 (완전/파편 둘 다 있어야 학습 가능)."
        )

    clf = RandomForestClassifier(
        n_estimators=300,
        class_weight="balanced",  # 완전/파편 비율이 안 맞을 수 있어서
        random_state=42,
        n_jobs=-1,
    )

    class_counts = {label: int((y == label).sum()) for label in set(y)}
    smallest_class_size = min(class_counts.values())
    n_splits = min(args.n_splits, smallest_class_size)
    if n_splits < args.n_splits:
        print(
            f"[안내] 가장 적은 클래스({smallest_class_size}건) 때문에 "
            f"n_splits을 {args.n_splits} -> {n_splits}로 낮춤"
        )
    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)

    # 교차검증 예측 - 학습에 쓰인 데이터로 그대로 평가하면 정확도가
    # 부풀려 보이므로, evaluate_completeness.py 때와 공정하게 비교하려면
    # 반드시 교차검증 예측을 써야 한다.
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
    print(
        "(규칙 기반 evaluate_completeness.py 결과: 49.1%, "
        "파편 재현율 5.2%였던 것과 비교해보세요)"
    )

    # 최종 모델은 전체 데이터로 다시 학습해서 저장 (실사용은 이걸로)
    clf.fit(X, y)
    importances = dict(zip(FEATURE_COLUMNS, clf.feature_importances_))
    print("\n=== 특징 중요도 ===")
    for feature, importance in sorted(
        importances.items(), key=lambda item: -item[1]
    ):
        print(f"  {feature}: {importance:.3f}")

    joblib.dump(clf, args.model_out)
    print(f"\n모델 저장: {args.model_out}")


if __name__ == "__main__":
    main()