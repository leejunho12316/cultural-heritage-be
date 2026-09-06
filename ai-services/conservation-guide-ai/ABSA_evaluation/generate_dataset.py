from dotenv import load_dotenv
load_dotenv()

import argparse
import base64
import json
import os
import random
from pathlib import Path

from openai import OpenAI

EVAL_DIR      = Path(__file__).resolve().parent
EXAMPLES_DIR  = EVAL_DIR / "eval_examples"
ORIGINALS_DIR = EVAL_DIR / "eval_original_photos"
OUTPUT_DIR    = EVAL_DIR / "eval_test_photos"

IMAGE_MODEL = "gpt-image-2"

# 모범 예시 (실제 강화처리 전/후 참고용) - eval_examples/ 로 이동된 이미지
REFERENCE_IMAGES = [
    EXAMPLES_DIR / "before_앞면.jpg",
    EXAMPLES_DIR / "before_뒷면.jpg",
    EXAMPLES_DIR / "after_앞면.jpg",
    EXAMPLES_DIR / "after_뒷면.jpg",
]

# 습윤 효과 테스트 스키마(app/schemas.py의 ColorChangeAnalysis)와 동일한 9개 항목
ASPECTS = {
    "hue_shift": "색상(색조) 변화 - 색 자체가 다른 색으로 옮겨갔는지",
    "brightness_change": "명도 변화 - 전체적으로 어두워지거나 밝아졌는지",
    "saturation_change": "채도 변화 - 색이 더 선명해지거나 탁해졌는지",
    "gloss_change": "광택 변화 - 무광이던 표면이 강화제 수지막 때문에 유광/광택이 도는 것으로 바뀌는지",
    "blanching": "백화현상 - 용제가 증발하면서 표면이 하얗게 뜨는 현상",
    "uneven_penetration": "얼룩/불균일 침투 - 강화제가 고르게 스며들지 않아 생기는 얼룩이나 경계 자국(tide-line)",
    "edge_visibility": "처리 경계 뚜렷함 - 처리 부위와 미처리 부위의 경계선이 도드라져 보이는지 (자연스럽게 섞여야 이상적)",
    "crack_response": "균열부 반응 - 균열/틈에 강화제가 고이거나 그 부분만 진해지거나 하얘지는지",
    "texture_change": "질감 변화 - 표면의 거칠기/매끄러움 등 촉감상 변화",
}

SEVERITIES = ["none", "mild", "moderate", "severe"]

SEVERITY_HINTS = {
    "none": "이 항목은 전/후 차이가 거의 없어야 함",
    "mild": "이 항목은 아주 미세하게만 변화해야 함 (자세히 봐야 알아챌 정도)",
    "moderate": "이 항목은 눈에 띄게 변화해야 함",
    "severe": "이 항목은 명확하고 강하게 변화해야 함",
}


def _reference_analysis_block() -> str:
    return """#모범 예시
eval_examples/ 폴더의 before_앞면.jpg, before_뒷면.jpg, after_앞면.jpg, after_뒷면.jpg는 강화처리 전/후 실제 참고 사진이야.
이 예시에서 관찰되는 변화는 다음과 같아 (색조/채도가 약간 있고 나머지는 거의 없는 편):
- hue_shift: mild
- brightness_change: mild
- saturation_change: moderate
- gloss_change: none
- blanching: none
- uneven_penetration: none
- edge_visibility: none
- crack_response: none
- texture_change: none
이 정도 강도감을 참고해서, 아래 목표 심각도에 맞는 자연스러운 강화처리 후 사진을 만들어줘."""


def random_target_severities(seed: int | None = None) -> dict:
    rng = random.Random(seed)
    severities = {aspect: rng.choice(SEVERITIES) for aspect in ASPECTS}

    # severe/moderate 항목이 2개를 초과하면 초과분을 none/mild로 교체
    high_aspects = [a for a, s in severities.items() if s in ("severe", "moderate")]
    if len(high_aspects) > 2:
        to_downgrade = rng.sample(high_aspects, len(high_aspects) - 2)
        for aspect in to_downgrade:
            severities[aspect] = rng.choice(("none", "mild"))

    return severities


def generate_after_photo(
    new_before_path: Path,
    target_severities: dict | None = None,
    seed: int | None = None,
) -> tuple[Path, Path]:
    if target_severities is None:
        target_severities = random_target_severities(seed)

    target_lines = "\n".join(
        f"{i}. {aspect} ({ASPECTS[aspect]}): 목표 심각도 = {target_severities[aspect]} "
        f"({SEVERITY_HINTS[target_severities[aspect]]})"
        for i, aspect in enumerate(ASPECTS, start=1)
    )

    prompt = f"""이 사진들은 유물 보존처리 과정 중 강화단계에서 유기용매에 희석한 강화제를 발랐을 때의 전/후 변화를 기록한 사진이야.

{_reference_analysis_block()}

#작업 대상
첫 번째 사진(새 유물의 강화처리 전 사진)을 기준으로, 같은 유물/같은 구도를 유지한 채로
강화처리를 마친 후의 사진을 만들어줘. 아래 9개 항목별로 지정된 목표 심각도를 정확히 반영해야 해.

{target_lines}

#규칙
- 유물의 형태, 각도, 배경은 원본(첫 번째 사진)과 동일하게 유지할 것.
- 위에 지정되지 않은 방식으로 임의로 다른 변화를 추가하지 말 것.
- 목표 심각도가 "none"인 항목은 해당 변화가 보이면 안 됨."""

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    image_paths = [new_before_path, *REFERENCE_IMAGES]
    open_files = [open(p, "rb") for p in image_paths]

    try:
        result = client.images.edit(
            model=IMAGE_MODEL,
            image=open_files,
            prompt=prompt,
        )
    finally:
        for f in open_files:
            f.close()

    image_bytes = base64.b64decode(result.data[0].b64_json)

    OUTPUT_DIR.mkdir(exist_ok=True)
    stem = new_before_path.stem
    image_out_path = OUTPUT_DIR / f"{stem}_after_generated.png"
    json_out_path  = OUTPUT_DIR / f"{stem}_ground_truth.json"

    image_out_path.write_bytes(image_bytes)
    json_out_path.write_text(
        json.dumps(target_severities, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"[generate_dataset] 생성된 사진: {image_out_path}")
    print(f"[generate_dataset] 정답 JSON:   {json_out_path}")

    return image_out_path, json_out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="eval_original_photos/ → eval_test_photos/ 이미지 쌍 생성"
    )
    parser.add_argument(
        "--count", type=int, default=10,
        help="생성할 이미지 쌍 수 (기본값: 10)"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="이미 생성된 쌍도 덮어쓰기"
    )
    args = parser.parse_args()

    candidates = sorted(
        list(ORIGINALS_DIR.glob("*.jpg")) +
        list(ORIGINALS_DIR.glob("*.jpeg")) +
        list(ORIGINALS_DIR.glob("*.png"))
    )

    if not candidates:
        print(f"[generate_dataset] eval_original_photos/ 에 이미지가 없습니다: {ORIGINALS_DIR}")
        raise SystemExit(1)

    selected = candidates[:args.count]
    print(f"[generate_dataset] {len(selected)}개 이미지 처리 시작 (전체 {len(candidates)}개 중)")

    for i, before_path in enumerate(selected):
        stem = before_path.stem
        out_img  = OUTPUT_DIR / f"{stem}_after_generated.png"
        out_json = OUTPUT_DIR / f"{stem}_ground_truth.json"

        if not args.force and out_img.exists() and out_json.exists():
            print(f"[generate_dataset] 스킵 (이미 존재): {stem}")
            continue

        print(f"[generate_dataset] [{i + 1}/{len(selected)}] {before_path.name} 처리 중...")
        generate_after_photo(new_before_path=before_path, seed=i)

    print("[generate_dataset] 완료!")
