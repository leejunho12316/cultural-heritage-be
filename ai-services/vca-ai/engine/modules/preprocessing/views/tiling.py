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

# 물리적으로 정확한(scale_unit_px 기반) 타일 크기를 그대로 쓸지 판단하는
# 상한 배수. 객체가 크고 scale_unit_px가 작으면(예: 눈금 하나가 77px인데
# 객체는 1800px) 물리적으로 정확한 크기를 그대로 써도 목표 개수의 10배 넘게
# 나올 수 있다 - tile_size_and_history에서 이 배수를 넘으면 물리적 크기를
# 버리고 목표 개수에 직접 맞춘 크기로 대체한다.
_OVERSHOOT_CEILING_MULTIPLE = 2


# 레인별로 스케일 단위(scale_unit_px) 대비 시도할 타일 한 변의 배수를
# 큰 것부터 순서대로 정의한다. tile_size_and_history에서 스케일 인식이
# high/medium일 때 타일 크기를 좁혀가며 호출된다.
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


# 레인별로 가장 큰 객체(largest_area)에 허용하는 목표 타일 개수 상한을
# 정의한다. target_count에서 객체 크기 비율에 곱해 실제 목표 개수를 낼 때
# 호출된다.
def _largest_target(lane: DetectorLane) -> int:
    match lane:
        case DetectorLane.OWLV2_SAM2:
            return 32
        case DetectorLane.GROUNDED_SAM2:
            return 18
        case DetectorLane.FLORENCE2_SAM2:
            return 8
        case DetectorLane.CLIPSEG:
            field = "detector_lanes"
            reason = "clipseg is non-active"
            raise ContractValidationError(field, reason)


# 한 축(가로 또는 세로)을 따라 OVERLAP_RATIO만큼 겹치는 타일 시작 좌표들을
# 생성하고, 마지막 타일이 끝까지 닿도록 종료 위치를 보정한다.
# tile_boxes에서 가로/세로 각각에 대해 호출된다.
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
    """scale span을 고르거나, 신뢰도가 낮으면 largest-object 폴백을 쓴다.

    scale-aware 사이징은 항상 가장 큰 span 하나만 시도한다(그 lane에 대해
    물리적으로 가장 충실한 타일 크기다) - 더 작은 span 배수로는 절대
    단계적으로 내려가지 않는다. 타일 크기를 절반으로 줄이면 2D에서 타일
    개수가 대략 4배가 되므로, 더 작은 이산 span은 거의 `target` 근처에
    떨어지지 않는다; 여전히 못 미치거나 몇 배씩 초과해버린다.
    물리적으로 정확한 span은 타일 개수가 `target`의
    `_OVERSHOOT_CEILING_MULTIPLE` 배수 이내에 들어올 때만 신뢰한다; 그
    범위를 벗어나면(어느 방향이든 - 물리적 scale_unit_px가 작은데 객체가
    크면 10배 이상 초과할 수도 있다) 더 작은 scale 배수를 추측하는 대신
    신뢰도가 낮은 경로와 같은 직접적인 target-count 공식으로 폴백해서,
    결과가 `target` 근처로 제한되고 못 미치거나 초과하지 않게 한다.
    """
    match request.scale_metadata.scale_confidence:
        case ScaleConfidence.HIGH | ScaleConfidence.MEDIUM:
            scale_unit = request.scale_metadata.scale_unit_px
            if scale_unit is None:
                field = "scale_unit_px"
                reason = "required for scale-aware tiling"
                raise ContractValidationError(field, reason)
            largest_span = _span_values(lane)[0]
            tile_size = largest_span * scale_unit
            tile_count = len(tile_boxes(object_target.bbox, tile_size))
            if target <= tile_count <= target * _OVERSHOOT_CEILING_MULTIPLE:
                return tile_size, (largest_span,), "scale_aware"
            tile_size = max(
                object_target.bbox.width, object_target.bbox.height
            ) / sqrt(target)
            return tile_size, (largest_span,), "scale_aware_target_capped"
        case ScaleConfidence.LOW | ScaleConfidence.UNAVAILABLE:
            tile_size = max(object_target.bbox.width, object_target.bbox.height) / sqrt(
                target
            )
            return tile_size, (), "largest_object_fallback"
