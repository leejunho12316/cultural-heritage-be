"""Detector and RAG lane contracts."""

from enum import StrEnum
from typing import Final

from modules.shared.errors import ContractValidationError


class DetectorLane(StrEnum):
    """Known detector lane identifiers, including the excluded compatibility lane."""

    OWLV2_SAM2 = "owlv2_sam2"
    FLORENCE2_SAM2 = "florence2_sam2"
    GROUNDED_SAM2 = "grounded_sam2"
    CLIPSEG = "clipseg"


class RagLane(StrEnum):
    """Active RAG lane identifiers."""

    OWLV2 = "owlv2"
    FLORENCE2 = "florence2"
    GROUNDINGDINO = "groundingdino"


ACTIVE_DETECTOR_LANES: Final[tuple[DetectorLane, ...]] = (
    DetectorLane.OWLV2_SAM2,
    DetectorLane.FLORENCE2_SAM2,
    DetectorLane.GROUNDED_SAM2,
)
ACTIVE_RAG_LANES: Final[tuple[RagLane, ...]] = (
    RagLane.OWLV2,
    RagLane.FLORENCE2,
    RagLane.GROUNDINGDINO,
)


def parse_detector_lane(raw_lane: str) -> DetectorLane:
    """Parse a detector lane string into its closed enum."""
    try:
        return DetectorLane(raw_lane)
    except ValueError as error:
        field = "detector_lane"
        raise ContractValidationError(field, raw_lane) from error


def validate_active_detector_lanes(
    lanes: tuple[DetectorLane, ...],
) -> tuple[DetectorLane, ...]:
    """Reject excluded or empty active detector lane selections."""
    if not lanes:
        field = "detector_lanes"
        raise ContractValidationError(field, "must not be empty")
    for lane in lanes:
        match lane:
            case DetectorLane.CLIPSEG:
                field = "detector_lanes"
                raise ContractValidationError(field, "clipseg is non-active")
            case DetectorLane.OWLV2_SAM2:
                continue
            case DetectorLane.FLORENCE2_SAM2:
                continue
            case DetectorLane.GROUNDED_SAM2:
                continue
    return lanes


def detector_to_rag_lane(detector_lane: DetectorLane) -> RagLane:
    """Map an active detector lane to its exact RAG lane."""
    match detector_lane:
        case DetectorLane.OWLV2_SAM2:
            return RagLane.OWLV2
        case DetectorLane.FLORENCE2_SAM2:
            return RagLane.FLORENCE2
        case DetectorLane.GROUNDED_SAM2:
            return RagLane.GROUNDINGDINO
        case DetectorLane.CLIPSEG:
            field = "detector_lane"
            reason = "clipseg has no active RAG lane"
            raise ContractValidationError(field, reason)
