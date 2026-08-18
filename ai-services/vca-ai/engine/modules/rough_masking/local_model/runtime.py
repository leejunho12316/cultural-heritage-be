"""Deferred heavyweight runtime adapters for rough-mask local models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from modules.rough_masking.local_model.inference import (
    detect_grounded as _detect_grounded,
)
from modules.rough_masking.local_model.inference import detect_owlv2 as _detect_owlv2
from modules.rough_masking.local_model.segmentation import LocalInferenceSettings
from modules.shared import ContractValidationError, DetectorLane

if TYPE_CHECKING:
    from pathlib import Path

    from modules.preprocessing.model_runtime.inventory import ModelInventoryEntry
    from modules.rough_masking.artifacts.materialization import MaskOutput
    from modules.rough_masking.contracts import AdapterRequest


class LaneRuntime(Protocol):
    """Runtime capability shared by all local rough-mask lanes."""

    def detect(
        self, request: AdapterRequest, image_path: Path
    ) -> tuple[MaskOutput, ...]:
        """Run detector/SAM2 and return anomaly masks for materialization."""
        ...


# "cpu"/"mps"/"cuda"/"cuda:N" 형식만 허용한다. build_lane_runtime에서
# 런타임을 선택하기 전에 호출된다.
def _validate_accelerator_device(device: str) -> None:
    normalized = device.lower()
    if normalized in {"cpu", "mps", "cuda"}:
        return
    if normalized.startswith("cuda:") and normalized.removeprefix("cuda:").isdecimal():
        return
    field = "device"
    reason = "rough_masking local runtime requires CUDA, MPS, or CPU"
    raise ContractValidationError(field, reason)


@dataclass(frozen=True, slots=True)
class _BaseLaneRuntime:
    detector_entry: ModelInventoryEntry
    sam2_entry: ModelInventoryEntry
    device: str

    # ROI 뷰의 coordinate_transform에서 source_bbox를 꺼내 로컬 추론 설정에
    # 싣는다. object_mask_path와 transform이 반드시 함께 있어야 하며, 이
    # bbox는 이후 _object_foreground_mask에서 크롭 좌표계로 그대로 쓰인다.
    def _settings(self, request: AdapterRequest) -> LocalInferenceSettings:
        transform = request.view.coordinate_transform
        if request.object_mask_path is None or transform is None:
            field = "object_mask_path"
            reason = "ROI foreground mask required for local rough masking"
            raise ContractValidationError(field, reason)
        bbox = transform.source_bbox
        return LocalInferenceSettings(
            detector_entry=self.detector_entry,
            sam2_entry=self.sam2_entry,
            device=self.device,
            max_mask_area_ratio=request.threshold_config.max_mask_area_ratio,
            object_mask_path=request.object_mask_path,
            roi_source_bbox=(bbox.left, bbox.top, bbox.width, bbox.height),
            apply_area_quality_gates=request.apply_area_quality_gates,
        )


@dataclass(frozen=True, slots=True)
class Owlv2Sam2Runtime(_BaseLaneRuntime):
    """OWLv2/SAM2 runtime behind the lightweight local runner seam."""

    def detect(
        self, request: AdapterRequest, image_path: Path
    ) -> tuple[MaskOutput, ...]:
        """Run the local OWLv2 and SAM2 inference path."""
        return _detect_owlv2(request, image_path, self._settings(request))


@dataclass(frozen=True, slots=True)
class GroundedSam2Runtime(_BaseLaneRuntime):
    """GroundingDINO/SAM2 runtime behind the lightweight local runner seam."""

    def detect(
        self, request: AdapterRequest, image_path: Path
    ) -> tuple[MaskOutput, ...]:
        """Run the local GroundingDINO and SAM2 inference path."""
        return _detect_grounded(request, image_path, self._settings(request))


def build_lane_runtime(
    *,
    lane: DetectorLane,
    detector_entry: ModelInventoryEntry,
    sam2_entry: ModelInventoryEntry,
    device: str,
) -> LaneRuntime:
    """Select the local heavy runtime adapter for one active lane."""
    _validate_accelerator_device(device)
    match lane:
        case DetectorLane.OWLV2_SAM2:
            return Owlv2Sam2Runtime(detector_entry, sam2_entry, device)
        case DetectorLane.GROUNDED_SAM2:
            return GroundedSam2Runtime(detector_entry, sam2_entry, device)
        case DetectorLane.FLORENCE2_SAM2:
            field = "lane"
            reason = "florence2_sam2 is not an active rough-mask lane"
            raise ContractValidationError(field, reason)
        case DetectorLane.CLIPSEG:
            field = "lane"
            reason = "CLIPSeg is not an active rough-mask lane"
            raise ContractValidationError(field, reason)
