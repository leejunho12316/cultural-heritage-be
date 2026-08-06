"""Plan foreground components for real object materialization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from modules.preprocessing.assets.mask_components import (
    MaskComponent,
    connected_mask_components,
    foreground_mask_from_rgb,
)
from modules.preprocessing.contracts.records import DetectionBox

if TYPE_CHECKING:
    from PIL import Image

MIN_FOREGROUND_COMPONENT_AREA_PX = 256


@dataclass(frozen=True, slots=True)
class ComponentPlan:
    """Foreground component and source detection geometry for one object."""

    component: MaskComponent
    detection: DetectionBox
    detection_crop_box: tuple[int, int, int, int]


def _component_detection(
    detection: DetectionBox,
    component: MaskComponent,
) -> DetectionBox:
    x0, y0, x1, y1 = component.bbox_xyxy
    offset_x = round(detection.x0)
    offset_y = round(detection.y0)
    return DetectionBox(
        x0=float(offset_x + x0),
        y0=float(offset_y + y0),
        x1=float(offset_x + x1),
        y1=float(offset_y + y1),
        score=detection.score,
        prompt_text=detection.prompt_text,
        generated_prompt_id=detection.generated_prompt_id,
    )


def component_plans(
    image: Image.Image,
    detections: tuple[DetectionBox, ...],
    foreground_white_threshold: int,
) -> tuple[ComponentPlan, ...]:
    """Create foreground component plans from detector candidates."""
    plans: list[ComponentPlan] = []
    for detection in detections:
        detection_crop_box = (
            round(detection.x0),
            round(detection.y0),
            round(detection.x1),
            round(detection.y1),
        )
        detection_crop = image.crop(detection_crop_box)
        foreground_mask = foreground_mask_from_rgb(
            np.asarray(detection_crop), foreground_white_threshold
        )
        components = connected_mask_components(
            foreground_mask, MIN_FOREGROUND_COMPONENT_AREA_PX
        )
        plans.extend(
            ComponentPlan(
                component=component,
                detection=_component_detection(detection, component),
                detection_crop_box=detection_crop_box,
            )
            for component in components
        )
    return tuple(plans)
