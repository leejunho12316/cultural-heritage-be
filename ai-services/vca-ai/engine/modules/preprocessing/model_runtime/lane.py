"""Lane validation and OWLv2 prompt selection for real preprocessing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from modules.prompt_generating import static_object_detection_pack
from modules.shared import ContractValidationError, DetectorLane

if TYPE_CHECKING:
    from modules.preprocessing.preflight.request import RunRequest
    from modules.prompt_generating import PromptRecord


def supported_real_lane(request: RunRequest) -> DetectorLane:
    """Return the only detector lane supported by real preprocessing v1."""
    if request.detector_lanes != (DetectorLane.OWLV2_SAM2,):
        field = "detector_lanes"
        reason = "real preprocessing v1 supports only owlv2_sam2"
        raise ContractValidationError(field, reason)
    return DetectorLane.OWLV2_SAM2


def owlv2_prompts() -> tuple[PromptRecord, ...]:
    """Select OWLv2 prompts from the object-detection seed pack."""
    return tuple(
        record
        for record in static_object_detection_pack.records
        if record.metadata.model_lane.value == "owlv2"
    )
