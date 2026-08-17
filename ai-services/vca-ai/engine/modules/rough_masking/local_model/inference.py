"""Deferred local-only detector inference for rough-mask lanes."""

# ruff: noqa: PLC0415
# pyright: reportAny=false, reportArgumentType=false, reportAttributeAccessIssue=false, reportCallIssue=false, reportMissingTypeStubs=false, reportReturnType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from modules.rough_masking.local_model.segmentation import (
    LocalDetection,
    LocalInferenceSettings,
    load_rgb_image,
    segment_detections,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from PIL import Image
    from transformers import (
        AutoModelForCausalLM,
        AutoModelForZeroShotObjectDetection,
        AutoProcessor,
        Owlv2ForObjectDetection,
        Owlv2Processor,
    )

    from modules.prompt_generating import PromptRecord
    from modules.rough_masking.artifacts.materialization import MaskOutput
    from modules.rough_masking.contracts import AdapterRequest


@dataclass(frozen=True, slots=True)
class _BoxPolicy:
    prompt: PromptRecord
    width_px: int
    height_px: int
    max_boxes: int


# rough_masking은 object-view + tile-view마다(런당 최대 수백 회) 같은 lane의
# detector를 다시 호출한다. 캐시가 없으면 매 호출마다 from_pretrained를 새로
# 실행해 디스크 IO와 로딩 시간이 그대로 누적되므로, (모델 디렉터리, 디바이스)
# 단위로 프로세스 수명 동안 재사용한다.
_OWLV2_CACHE: dict[tuple[str, str], tuple[Owlv2Processor, Owlv2ForObjectDetection]] = {}
_FLORENCE2_CACHE: dict[tuple[str, str], tuple[AutoProcessor, AutoModelForCausalLM]] = {}
_GROUNDED_CACHE: dict[
    tuple[str, str], tuple[AutoProcessor, AutoModelForZeroShotObjectDetection]
] = {}


def load_owlv2_detector(
    model_dir: Path, device: str
) -> tuple[Owlv2Processor, Owlv2ForObjectDetection]:
    """Load OWLv2 exclusively from its inventory directory, reused across calls."""
    cache_key = (str(model_dir), device)
    cached = _OWLV2_CACHE.get(cache_key)
    if cached is not None:
        return cached

    from transformers import Owlv2ForObjectDetection, Owlv2Processor

    processor = Owlv2Processor.from_pretrained(
        model_dir, local_files_only=True, use_fast=False
    )
    model = Owlv2ForObjectDetection.from_pretrained(
        model_dir, local_files_only=True
    ).to(device)
    _ = model.eval()
    _OWLV2_CACHE[cache_key] = (processor, model)
    return processor, model


def load_florence2_detector(
    model_dir: Path, device: str
) -> tuple[AutoProcessor, AutoModelForCausalLM]:
    """Load Florence-2 from a local cache without a remote model identifier, reused across calls."""
    cache_key = (str(model_dir), device)
    cached = _FLORENCE2_CACHE.get(cache_key)
    if cached is not None:
        return cached

    from transformers import AutoModelForCausalLM, AutoProcessor

    processor = AutoProcessor.from_pretrained(
        model_dir, local_files_only=True, trust_remote_code=True
    )
    model = AutoModelForCausalLM.from_pretrained(
        model_dir,
        attn_implementation="eager",
        local_files_only=True,
        trust_remote_code=True,
    ).to(device)
    _ = model.eval()
    _FLORENCE2_CACHE[cache_key] = (processor, model)
    return processor, model


def load_grounded_detector(
    model_dir: Path, device: str
) -> tuple[AutoProcessor, AutoModelForZeroShotObjectDetection]:
    """Load Hugging Face GroundingDINO artifacts from the local inventory path, reused across calls."""
    cache_key = (str(model_dir), device)
    cached = _GROUNDED_CACHE.get(cache_key)
    if cached is not None:
        return cached

    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

    processor = AutoProcessor.from_pretrained(model_dir, local_files_only=True)
    model = AutoModelForZeroShotObjectDetection.from_pretrained(
        model_dir, local_files_only=True
    ).to(device)
    _ = model.eval()
    _GROUNDED_CACHE[cache_key] = (processor, model)
    return processor, model


def bounded_detections(
    scores: Sequence[float], boxes: Sequence[Sequence[float]], policy: _BoxPolicy
) -> tuple[LocalDetection, ...]:
    """Clamp sorted detector boxes to the ROI and apply the per-prompt cap."""
    detections: list[LocalDetection] = []
    rows = sorted(
        zip(scores, boxes, strict=True), key=lambda row: float(row[0]), reverse=True
    )
    for score, box in rows:
        left = max(float(box[0]), 0.0)
        top = max(float(box[1]), 0.0)
        right = min(float(box[2]), float(policy.width_px))
        bottom = min(float(box[3]), float(policy.height_px))
        if right <= left or bottom <= top:
            continue
        detections.append(
            LocalDetection(
                prompt=policy.prompt,
                score=float(score),
                bbox_xyxy=(left, top, right, bottom),
            )
        )
        if len(detections) >= policy.max_boxes:
            break
    return tuple(detections)


# open-vocabulary 디텍터(OWLv2/GroundingDINO/Florence-2)는 확신이 높은 특징
# 하나에 대해 거의 같은 위치의 박스를 여러 개 내놓는 경우가 드물지 않은데,
# threshold 필터링만으로는 이걸 못 거른다 - post_process_grounded_object_detection이
# NMS를 포함하지 않기 때문이다(실측: mask_refining 재탐지에서 IoU 0.9999인
# 박스 두 개가 각각 별도 "특이점"으로 리포트까지 새어나감). 예전엔 이 뒤에
# 있던 post-refinement relation-authority가 안전망 역할을 했지만, 그 단계가
# 병합 로직 단순화로 없어졌으므로 발생 지점(디텍터 후처리)에서 직접 막는다.
# 프롬프트 하나짜리 호출(_owlv2_detections/_grounded_detections 등)뿐 아니라,
# 같은 이미지에 여러 프롬프트를 순차 실행해 합친 결과에도 적용해야 한다 -
# 서로 다른 프롬프트가 같은 자리를 각자 잡아내는 경우도 실제로 있었다.
_NMS_IOU_THRESHOLD: Final = 0.5


def _box_iou(
    left: tuple[float, float, float, float], right: tuple[float, float, float, float]
) -> float:
    lx0, ly0, lx1, ly1 = left
    rx0, ry0, rx1, ry1 = right
    ix0, iy0 = max(lx0, rx0), max(ly0, ry0)
    ix1, iy1 = min(lx1, rx1), min(ly1, ry1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    intersection = (ix1 - ix0) * (iy1 - iy0)
    union = (lx1 - lx0) * (ly1 - ly0) + (rx1 - rx0) * (ry1 - ry0) - intersection
    return intersection / union if union > 0 else 0.0


def suppress_near_duplicate_detections(
    detections: tuple[LocalDetection, ...],
) -> tuple[LocalDetection, ...]:
    """Greedily drop lower-score boxes that overlap an already-kept one (IoU-based NMS)."""
    ordered = sorted(detections, key=lambda item: item.score, reverse=True)
    kept: list[LocalDetection] = []
    for candidate in ordered:
        if any(
            _box_iou(candidate.bbox_xyxy, accepted.bbox_xyxy) > _NMS_IOU_THRESHOLD
            for accepted in kept
        ):
            continue
        kept.append(candidate)
    return tuple(kept)


def box_policy(request: AdapterRequest, prompt: PromptRecord) -> _BoxPolicy:
    """Bind a single locked prompt to the ROI geometry and detector cap."""
    return _BoxPolicy(
        prompt=prompt,
        width_px=request.image_width_px,
        height_px=request.image_height_px,
        max_boxes=request.threshold_config.max_boxes_per_prompt,
    )


# OWLv2 프로세서로 프롬프트별 zero-shot 탐지를 실행하고 box_threshold로
# 걸러진 박스를 bounded_detections로 ROI 클램프/상한 적용한다.
def _owlv2_detections(
    request: AdapterRequest,
    image: Image.Image,
    settings: LocalInferenceSettings,
) -> tuple[LocalDetection, ...]:
    import torch

    processor, model = load_owlv2_detector(
        settings.detector_entry.local_dir, settings.device
    )
    detections: list[LocalDetection] = []
    for prompt in request.prompts:
        inputs = processor(
            text=[[prompt.prompt_text]], images=image, return_tensors="pt"
        ).to(settings.device)
        with torch.inference_mode():
            model_output = model(**inputs)
        result = processor.post_process_grounded_object_detection(
            outputs=model_output,
            target_sizes=torch.tensor(
                [(request.image_height_px, request.image_width_px)],
                device=settings.device,
            ),
            threshold=request.threshold_config.box_threshold,
            text_labels=[[prompt.prompt_text]],
        )[0]
        detections.extend(
            bounded_detections(
                result["scores"], result["boxes"], box_policy(request, prompt)
            )
        )
    return suppress_near_duplicate_detections(tuple(detections))


# Florence-2를 <CAPTION_TO_PHRASE_GROUNDING> 태스크로 실행해 프롬프트별
# 박스를 얻는다. (아래 uniform score 관련 주석 참고)
def _florence2_detections(
    request: AdapterRequest,
    image: Image.Image,
    settings: LocalInferenceSettings,
) -> tuple[LocalDetection, ...]:
    import torch

    processor, model = load_florence2_detector(
        settings.detector_entry.local_dir, settings.device
    )
    task_prompt = "<CAPTION_TO_PHRASE_GROUNDING>"
    detections: list[LocalDetection] = []
    for prompt in request.prompts:
        inputs = processor(
            text=f"{task_prompt} {prompt.prompt_text}",
            images=image,
            return_tensors="pt",
        ).to(settings.device)
        with torch.inference_mode():
            generated_ids = model.generate(
                **inputs,
                max_new_tokens=1024,
                do_sample=False,
                use_cache=False,
            )
        generated_text = processor.batch_decode(
            generated_ids, skip_special_tokens=False
        )[0]
        answer = processor.post_process_generation(
            generated_text,
            task=task_prompt,
            image_size=(request.image_width_px, request.image_height_px),
        )[task_prompt]
        boxes = answer["bboxes"]
        # Florence-2 phrase grounding은 박스별 confidence를 내지 않으므로
        # 점수를 모두 1.0으로 채운다. 그 결과 bounded_detections의 상한 컷은
        # 다른 두 레인과 달리 점수 정렬이 아닌 모델이 반환한 순서를 그대로 따른다.
        detections.extend(
            bounded_detections([1.0] * len(boxes), boxes, box_policy(request, prompt))
        )
    return suppress_near_duplicate_detections(tuple(detections))


# GroundingDINO를 box_threshold/text_threshold 두 임계값으로 실행하고
# 결과를 bounded_detections로 정리한다.
def _grounded_detections(
    request: AdapterRequest,
    image: Image.Image,
    settings: LocalInferenceSettings,
) -> tuple[LocalDetection, ...]:
    import torch

    processor, model = load_grounded_detector(
        settings.detector_entry.local_dir, settings.device
    )
    detections: list[LocalDetection] = []
    for prompt in request.prompts:
        inputs = processor(
            images=image, text=[[prompt.prompt_text]], return_tensors="pt"
        ).to(settings.device)
        with torch.inference_mode():
            model_output = model(**inputs)
        result = processor.post_process_grounded_object_detection(
            model_output,
            inputs.input_ids,
            threshold=request.threshold_config.box_threshold,
            text_threshold=request.threshold_config.text_threshold,
            target_sizes=[(request.image_height_px, request.image_width_px)],
        )[0]
        detections.extend(
            bounded_detections(
                result["scores"], result["boxes"], box_policy(request, prompt)
            )
        )
    return suppress_near_duplicate_detections(tuple(detections))


def _segment(
    detections: tuple[LocalDetection, ...],
    image: Image.Image,
    settings: LocalInferenceSettings,
) -> tuple[MaskOutput, ...]:
    if not detections:
        return ()
    return segment_detections(detections, image, settings)


def detect_owlv2(
    request: AdapterRequest, image_path: Path, settings: LocalInferenceSettings
) -> tuple[MaskOutput, ...]:
    """Run local OWLv2 detection followed by SAM2 box segmentation."""
    image = load_rgb_image(image_path)
    return _segment(_owlv2_detections(request, image, settings), image, settings)


def detect_florence2(
    request: AdapterRequest, image_path: Path, settings: LocalInferenceSettings
) -> tuple[MaskOutput, ...]:
    """Run prompt-scoped local Florence-2 detection followed by SAM2."""
    image = load_rgb_image(image_path)
    return _segment(_florence2_detections(request, image, settings), image, settings)


def detect_grounded(
    request: AdapterRequest, image_path: Path, settings: LocalInferenceSettings
) -> tuple[MaskOutput, ...]:
    """Run local Transformers GroundingDINO detection followed by SAM2."""
    image = load_rgb_image(image_path)
    return _segment(_grounded_detections(request, image, settings), image, settings)
