"""Scale-aware tile geometry and lane-specific planning calculations."""

from math import ceil, sqrt

from modules.preprocessing.contracts.views import (
    BoundingBox,
    ObjectTarget,
    ScaleConfidence,
    ViewPlanningRequest,
)
from modules.shared import ContractValidationError, DetectorLane

OVERLAP_RATIO = 0.33


def _span_values(lane: DetectorLane) -> tuple[float, ...]:
    match lane:
        case DetectorLane.OWLV2_SAM2:
            return (2.0, 1.0, 0.5)
        case DetectorLane.GROUNDED_SAM2:
            return (3.0, 1.5, 0.75)
        case DetectorLane.FLORENCE2_SAM2:
            return (5.0, 2.5, 1.25)
        case DetectorLane.CLIPSEG:
            field = "detector_lanes"
            reason = "clipseg is non-active"
            raise ContractValidationError(field, reason)


def _largest_target(lane: DetectorLane) -> int:
    match lane:
        case DetectorLane.OWLV2_SAM2:
            return 64
        case DetectorLane.GROUNDED_SAM2:
            return 36
        case DetectorLane.FLORENCE2_SAM2:
            return 16
        case DetectorLane.CLIPSEG:
            field = "detector_lanes"
            reason = "clipseg is non-active"
            raise ContractValidationError(field, reason)


def _positions(start: float, length: float, tile_size: float) -> tuple[float, ...]:
    if tile_size >= length:
        return (start,)
    stride = tile_size * (1 - OVERLAP_RATIO)
    positions: list[float] = [start]
    last_start = start + length - tile_size
    while positions[-1] + stride < last_start:
        positions.append(positions[-1] + stride)
    if positions[-1] != last_start:
        positions.append(last_start)
    return tuple(positions)


def tile_boxes(bbox: BoundingBox, tile_size: float) -> tuple[BoundingBox, ...]:
    """Create overlap-preserving square boxes confined to an object bbox."""
    return tuple(
        BoundingBox(left, top, min(tile_size, bbox.width), min(tile_size, bbox.height))
        for top in _positions(bbox.top, bbox.height, tile_size)
        for left in _positions(bbox.left, bbox.width, tile_size)
    )


def target_count(object_area: float, largest_area: float, lane: DetectorLane) -> int:
    """Compute an untruncated object-size-adjusted tile target for one lane."""
    return max(4, ceil(_largest_target(lane) * sqrt(object_area / largest_area)))


def tile_size_and_history(
    request: ViewPlanningRequest,
    object_target: ObjectTarget,
    lane: DetectorLane,
    target: int,
) -> tuple[float, tuple[float, ...], str]:
    """Choose scale spans or the low-confidence largest-object fallback."""
    match request.scale_metadata.scale_confidence:
        case ScaleConfidence.HIGH | ScaleConfidence.MEDIUM:
            scale_unit = request.scale_metadata.scale_unit_px
            if scale_unit is None:
                field = "scale_unit_px"
                reason = "required for scale-aware tiling"
                raise ContractValidationError(field, reason)
            history: list[float] = []
            spans = _span_values(lane)
            tile_size = spans[-1] * scale_unit
            for span in spans:
                history.append(span)
                tile_size = span * scale_unit
                if len(tile_boxes(object_target.bbox, tile_size)) >= target:
                    return tile_size, tuple(history), "scale_aware"
            return tile_size, tuple(history), "scale_aware"
        case ScaleConfidence.LOW | ScaleConfidence.UNAVAILABLE:
            tile_size = max(object_target.bbox.width, object_target.bbox.height) / sqrt(
                target
            )
            return tile_size, (), "largest_object_fallback"
