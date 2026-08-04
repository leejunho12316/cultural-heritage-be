"""
train_pottery_multitask.py

manifest.csv(image_path, pattern_type, era, color_group)로 ResNet18을
파인튜닝해서 문양/시대/색상 세 가지를 동시에 예측하는 멀티태스크
분류기를 학습한다.

Training 폴더에서 만든 manifest로 학습하고, Validation 폴더에서 만든
manifest로 평가한다 (AI Hub가 준 분할을 그대로 씀 - 섞어서 다시
나누지 않음).

3060Ti(8GB) 기준: 이미지넷 프리트레인 ResNet18 + 224x224 해상도 +
배치 16~32 정도면 여유 있게 돌아간다. 처음부터 학습이 아니라
전이학습이라 데이터도 적게 필요하고 빠르다.

사용법:
    python train_pottery_multitask.py \
        --train-manifest manifest_train.csv \
        --val-manifest manifest_val.csv \
        --epochs 15 --batch-size 32 --out-dir ./pottery_multitask_model
"""

import argparse
import json
from pathlib import Path

import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms, models
from PIL import Image
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import classification_report


TASKS = ["pattern_type", "era", "color_group"]

TRAIN_TRANSFORM = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.RandomHorizontalFlip(),
    transforms.ColorJitter(brightness=0.1, contrast=0.1, saturation=0.1),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])

EVAL_TRANSFORM = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])


class PotteryDataset(Dataset):
    def __init__(self, df: pd.DataFrame, encoders: dict[str, LabelEncoder], transform):
        self.df = df.reset_index(drop=True)
        self.encoders = encoders
        self.transform = transform

    def __len__(self) -> int:
        return len(self.df)

    def __getitem__(self, idx: int):
        row = self.df.iloc[idx]
        image = Image.open(row["image_path"]).convert("RGB")
        image = self.transform(image)

        labels = {
            task: int(self.encoders[task].transform([row[task]])[0]) for task in TASKS
        }
        return image, labels


class MultiTaskResNet(nn.Module):
    def __init__(self, num_classes: dict[str, int]):
        super().__init__()
        backbone = models.resnet18(weights=models.ResNet18_Weights.IMAGENET1K_V1)
        in_features = backbone.fc.in_features
        backbone.fc = nn.Identity()
        self.backbone = backbone

        self.heads = nn.ModuleDict({
            task: nn.Linear(in_features, n) for task, n in num_classes.items()
        })

    def forward(self, x):
        features = self.backbone(x)
        return {task: head(features) for task, head in self.heads.items()}


def build_encoders(train_df: pd.DataFrame) -> dict[str, LabelEncoder]:
    encoders = {}
    for task in TASKS:
        encoder = LabelEncoder()
        encoder.fit(train_df[task])
        encoders[task] = encoder
    return encoders


def run_epoch(model, loader, device, criterion, optimizer=None):
    is_train = optimizer is not None
    model.train() if is_train else model.eval()

    total_loss = 0.0
    all_preds = {task: [] for task in TASKS}
    all_labels = {task: [] for task in TASKS}

    with torch.set_grad_enabled(is_train):
        for images, labels in loader:
            images = images.to(device)
            labels = {task: labels[task].to(device) for task in TASKS}

            outputs = model(images)
            loss = sum(criterion(outputs[task], labels[task]) for task in TASKS)

            if is_train:
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

            total_loss += loss.item() * images.size(0)

            for task in TASKS:
                preds = outputs[task].argmax(dim=1).detach().cpu().tolist()
                all_preds[task].extend(preds)
                all_labels[task].extend(labels[task].detach().cpu().tolist())

    avg_loss = total_loss / len(loader.dataset)
    return avg_loss, all_preds, all_labels


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-manifest", type=str, required=True)
    parser.add_argument("--val-manifest", type=str, required=True)
    parser.add_argument("--epochs", type=int, default=15)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--out-dir", type=str, default="./pottery_multitask_model")
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"device: {device}")

    train_df = pd.read_csv(args.train_manifest)
    val_df = pd.read_csv(args.val_manifest)
    print(f"학습 {len(train_df)}건 / 평가 {len(val_df)}건")

    encoders = build_encoders(train_df)

    # val에만 있고 train에는 없는 클래스가 있으면 인코더가 모르는 값이라
    # 에러가 난다 - 미리 걸러서 알려준다.
    for task in TASKS:
        val_only = set(val_df[task]) - set(train_df[task])
        if val_only:
            print(f"[경고] {task}에서 학습 데이터에 없는 평가 클래스 발견: {val_only} - 해당 행 제외")
            val_df = val_df[~val_df[task].isin(val_only)]

    train_dataset = PotteryDataset(train_df, encoders, TRAIN_TRANSFORM)
    val_dataset = PotteryDataset(val_df, encoders, EVAL_TRANSFORM)

    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True, num_workers=2)
    val_loader = DataLoader(val_dataset, batch_size=args.batch_size, shuffle=False, num_workers=2)

    num_classes = {task: len(encoders[task].classes_) for task in TASKS}
    print(f"클래스 수: {num_classes}")

    model = MultiTaskResNet(num_classes).to(device)
    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    best_val_loss = float("inf")

    for epoch in range(1, args.epochs + 1):
        train_loss, _, _ = run_epoch(model, train_loader, device, criterion, optimizer)
        val_loss, val_preds, val_labels = run_epoch(model, val_loader, device, criterion)

        print(f"[epoch {epoch}/{args.epochs}] train_loss={train_loss:.4f} val_loss={val_loss:.4f}")

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            torch.save(model.state_dict(), out_dir / "best_model.pt")
            print("  -> best_model.pt 저장")

    # 최종 리포트 (best 기준이 아니라 마지막 epoch 기준 - 필요하면 best_model.pt 로드해서 재평가할 것)
    print("\n=== 최종 epoch 기준 태스크별 분류 리포트 ===")
    for task in TASKS:
        print(f"\n--- {task} ---")
        target_names = list(encoders[task].classes_)
        print(classification_report(
            val_labels[task], val_preds[task], target_names=target_names, zero_division=0
        ))

    # 인코더 저장 (추론할 때 클래스 인덱스 -> 이름 복원용)
    encoder_map = {task: list(encoders[task].classes_) for task in TASKS}
    with open(out_dir / "label_encoders.json", "w", encoding="utf-8-sig") as f:
        json.dump(encoder_map, f, ensure_ascii=False, indent=2)

    print(f"\n모델/인코더 저장 위치: {out_dir.resolve()}")


if __name__ == "__main__":
    main()