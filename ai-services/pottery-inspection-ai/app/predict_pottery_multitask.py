"""
predict_pottery_multitask.py

best_model.pt + label_encoders.json만 있으면 되는 추론 전용 스크립트.
학습 이미지도, GPU X live demo용 Good

사용법:
    python predict_pottery_multitask.py --image new_pottery.jpg --model-dir ./pottery_multitask_model_v2
"""

import argparse
import json
from pathlib import Path

import torch
import torch.nn as nn
from torchvision import transforms, models
from PIL import Image

TASKS = ["pattern_type", "era", "color_group"]

EVAL_TRANSFORM = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])


class MultiTaskResNet(nn.Module):
    def __init__(self, num_classes: dict[str, int]):
        super().__init__()
        # 추론만 할 거라 프리트레인 가중치를 인터넷에서 새로 받을 필요 없음
        backbone = models.resnet18(weights=None)
        in_features = backbone.fc.in_features
        backbone.fc = nn.Identity()
        self.backbone = backbone

        self.heads = nn.ModuleDict({
            task: nn.Linear(in_features, n) for task, n in num_classes.items()
        })

    def forward(self, x):
        features = self.backbone(x)
        return {task: head(features) for task, head in self.heads.items()}


def load_model(model_dir: Path) -> tuple[MultiTaskResNet, dict[str, list[str]]]:
    with open(model_dir / "label_encoders.json", encoding="utf-8-sig") as f:
        class_names = json.load(f)

    num_classes = {task: len(class_names[task]) for task in TASKS}

    model = MultiTaskResNet(num_classes)
    state_dict = torch.load(
        model_dir / "best_model.pt", map_location=torch.device("cpu")
    )
    model.load_state_dict(state_dict)
    model.eval()

    return model, class_names


def predict(image_path: str, model_dir: str) -> dict:
    model, class_names = load_model(Path(model_dir))

    image = Image.open(image_path).convert("RGB")
    tensor = EVAL_TRANSFORM(image).unsqueeze(0)  # 배치 차원 추가

    with torch.no_grad():
        outputs = model(tensor)

    result = {}
    for task in TASKS:
        probs = torch.softmax(outputs[task], dim=1)[0]
        top_idx = int(probs.argmax())
        result[task] = {
            "prediction": class_names[task][top_idx],
            "confidence": round(float(probs[top_idx]), 3),
            "all_probs": {
                class_names[task][i]: round(float(p), 3)
                for i, p in enumerate(probs)
            },
        }

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=str, required=True)
    parser.add_argument("--model-dir", type=str, required=True)
    args = parser.parse_args()

    result = predict(args.image, args.model_dir)

    print(f"\n=== {args.image} 예측 결과 ===")
    for task in TASKS:
        r = result[task]
        print(f"{task}: {r['prediction']} (확신도 {r['confidence']:.1%})")

    print("\n(전체 확률 분포는 --debug로 필요하면 코드에서 result 그대로 확인 가능)")