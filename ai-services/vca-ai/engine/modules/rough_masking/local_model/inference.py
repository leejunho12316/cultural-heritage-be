"""Deferred local-only detector inference for rough-mask lanes."""

# ruff: noqa: PLC0415
# pyright: reportAny=false, reportArgumentType=false, reportAttributeAccessIssue=false, reportCallIssue=false, reportMissingTypeStubs=false, reportReturnType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

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


def load_owlv2_detector(
    model_dir: Path, device: str
) -> tuple[Owlv2Processor, Owlv2ForObjectDetection]:
    """Load OWLv2 exclusively from its inventory directory."""
    from transformers import Owlv2ForObjectDetection, Owlv2Processor

    processor = Owlv2Processor.from_pretrained(
        model_dir, local_files_only=True, use_fast=False
    )
    model = Owlv2ForObjectDetection.from_pretrained(
        model_dir, local_files_only=True
    ).to(device)
    _ = model.eval()
    return processor, model


def load_florence2_detector(
    model_dir: Path, device: str
) -> tuple[AutoProcessor, AutoModelForCausalLM]:
    """Load Florence-2 from a local cache without a remote model identifier."""
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
    return processor, model


def load_grounded_detector(
    model_dir: Path, device: str
) -> tuple[AutoProcessor, AutoModelForZeroShotObjectDetection]:
    """Load Hugging Face GroundingDINO artifacts from the local inventory path."""
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

    processor = AutoProcessor.from_pretrained(model_dir, local_files_only=True)
    model = AutoModelForZeroShotObjectDetection.from_pretrained(
        model_dir, local_files_only=True
    ).to(device)
    _ = model.eval()
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


def box_policy(request: AdapterRequest, prompt: PromptRecord) -> _BoxPolicy:
    """Bind a single locked prompt to the ROI geometry and detector cap."""
    return _BoxPolicy(
        prompt=prompt,
        width_px=request.image_width_px,
        height_px=request.image_height_px,
        max_boxes=request.threshold_config.max_boxes_per_prompt,
    )


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
    return tuple(detections)


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
        detections.extend(
            bounded_detections([1.0] * len(boxes), boxes, box_policy(request, prompt))
        )
    return tuple(detections)


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
    return tuple(detections)


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
