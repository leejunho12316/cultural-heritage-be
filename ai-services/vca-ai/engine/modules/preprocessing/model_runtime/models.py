"""Heavy model loading and detector execution for preprocessing."""

# pyright: reportAny=false, reportArgumentType=false, reportCallIssue=false, reportMissingTypeStubs=false, reportReturnType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

from os import PathLike
from typing import TYPE_CHECKING, Protocol

import torch
from PIL import Image
from sam2.build_sam import build_sam2
from sam2.sam2_image_predictor import SAM2ImagePredictor
from torch import Tensor
from transformers import (
    Owlv2ForObjectDetection,
    Owlv2Processor,
)

from modules.preprocessing.contracts.records import (
    OWLV2_NATIVE_INPUT_SIZE,
    DetectionBox,
    DetectionRun,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import numpy as np
    from numpy.typing import NDArray

    from modules.preprocessing.model_runtime.inventory import ModelInventoryEntry


ModelPath = str | PathLike[str]


class ImageProcessorSettings(Protocol):
    """Mutable image processor settings used by OWLv2 preprocessing."""

    size: dict[str, int]


class OwlProcessor(Protocol):
    """Structural subset of the OWLv2 processor used by this pipeline."""

    image_processor: ImageProcessorSettings

    def __call__(
        self, *, text: list[list[str]], images: Image.Image, return_tensors: str
    ) -> dict[str, Tensor]:
        """Build model inputs from prompts and an image."""
        ...

    def post_process_grounded_object_detection(
        self,
        *,
        outputs: Tensor,
        target_sizes: Tensor,
        threshold: float,
        text_labels: list[list[str]],
    ) -> list[dict[str, Tensor]]:
        """Convert raw OWLv2 outputs to scored boxes."""
        ...


class OwlModel(Protocol):
    """Structural subset of the OWLv2 detector used by this pipeline."""

    def __call__(self, **inputs: Tensor | bool) -> Tensor:
        """Run object detection."""
        ...

    def parameters(self) -> Iterator[Tensor]:
        """Yield model parameters for runtime device lookup."""
        ...


class SamPredictor(Protocol):
    """Structural subset of SAM2 image prediction used by materialization."""

    def set_image(self, image: NDArray[np.uint8]) -> None:
        """Set the image array for subsequent mask prediction."""
        ...

    def predict(
        self, *, box: NDArray[np.float64], multimask_output: bool
    ) -> tuple[tuple[NDArray[np.bool_], ...], NDArray[np.float32], Tensor]:
        """Predict a mask for one bounding box."""
        _ = box, multimask_output
        raise NotImplementedError


def load_real_models(
    model_entries: dict[str, ModelInventoryEntry], device: str
) -> tuple[Owlv2Processor, Owlv2ForObjectDetection, SamPredictor]:
    """Load local OWLv2 and SAM2 models from the shared cache."""
    runtime_device = torch.device(device)
    owlv2_path: ModelPath = model_entries["owlv2_sam2.detector"].local_dir
    sam2_path = model_entries["sam2.segmenter"].local_dir
    owlv2_processor = Owlv2Processor.from_pretrained(
        owlv2_path, local_files_only=True, use_fast=False
    )
    owlv2_model = Owlv2ForObjectDetection.from_pretrained(
        owlv2_path, local_files_only=True
    ).to(runtime_device)
    sam2_model = build_sam2(
        "sam2_hiera_l.yaml",
        str(sam2_path / "sam2_hiera_large.pt"),
        device=device,
    )
    return owlv2_processor, owlv2_model, SAM2ImagePredictor(sam2_model)


def detect_boxes[TProcessor: OwlProcessor, TModel: OwlModel](
    image_path: Path,
    detection_run: DetectionRun[TProcessor, TModel],
) -> tuple[DetectionBox, ...]:
    """Detect artifact candidate boxes using OWLv2 prompts."""
    image = Image.open(image_path).convert("RGB")
    prompts = detection_run.prompts
    texts = [[prompt.prompt_text for prompt in prompts]]
    detector_input_size = detection_run.detector_input_size
    if detector_input_size != OWLV2_NATIVE_INPUT_SIZE:
        # processor는 pipeline.py의 루프에서 이미지마다 재사용되는 공유
        # 상태이며, 이 오버라이드는 네이티브 크기로 되돌려지지 않는다.
        # 따라서 이후 네이티브 크기를 원하는 이미지가 와도 이전에 남은
        # 비-네이티브 설정으로 리사이즈될 수 있다.
        detection_run.processor.image_processor.size = {
            "height": detector_input_size,
            "width": detector_input_size,
        }
    inputs = detection_run.processor(text=texts, images=image, return_tensors="pt")
    model_device = next(detection_run.model.parameters()).device
    inputs = {key: value.to(model_device) for key, value in inputs.items()}
    with torch.no_grad():
        if detector_input_size == OWLV2_NATIVE_INPUT_SIZE:
            outputs = detection_run.model(**inputs)
        else:
            outputs = detection_run.model(**inputs, interpolate_pos_encoding=True)
    target_sizes = torch.tensor([(image.height, image.width)], device=model_device)
    result = detection_run.processor.post_process_grounded_object_detection(
        outputs=outputs,
        target_sizes=target_sizes,
        threshold=detection_run.threshold,
        text_labels=texts,
    )[0]
    detections: list[DetectionBox] = []
    rows = zip(result["scores"], result["boxes"], strict=False)
    for score, box in sorted(rows, key=lambda row: float(row[0]), reverse=True):
        if (
            detection_run.max_detections is not None
            and len(detections) >= detection_run.max_detections
        ):
            break
        raw_x0, raw_y0, raw_x1, raw_y1 = (float(value.cpu()) for value in box)
        x0 = max(raw_x0, 0.0)
        y0 = max(raw_y0, 0.0)
        x1 = min(raw_x1, float(image.width))
        y1 = min(raw_y1, float(image.height))
        if x1 <= x0 or y1 <= y0:
            continue
        detections.append(
            DetectionBox(
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
                score=float(score.cpu()),
                prompt_text=prompts[0].prompt_text,
                generated_prompt_id=prompts[0].metadata.generated_prompt_id,
            )
        )
    return tuple(detections)
