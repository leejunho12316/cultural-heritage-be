"""Detector candidate deduplication for overlapping boxes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from modules.preprocessing.contracts.records import DetectionBox

if TYPE_CHECKING:
    from modules.preprocessing.contracts.views import BoundingBox

EDGE_ADJACENCY_GAP: Final = 16.0
MIN_ORTHOGONAL_OVERLAP_RATIO: Final = 0.25
BROAD_BOX_MIN_AREA_RATIO: Final = 0.90
BROAD_BOX_MIN_WIDTH_RATIO: Final = 0.95
BROAD_BOX_MIN_HEIGHT_RATIO: Final = 0.95
NMS_IOU_THRESHOLD: Final = 0.1
NMS_CONTAINMENT_THRESHOLD: Final = 0.99


class DetectionMergeStrategy(StrEnum):
    """Available same-prompt candidate deduplication modes."""

    UNION = "union"
    NMS = "nms"


@dataclass(frozen=True, slots=True)
class DetectionMergeOptions:
    """Tuning parameters for detector candidate deduplication."""

    strategy: DetectionMergeStrategy = DetectionMergeStrategy.UNION
    edge_adjacency_gap: float = EDGE_ADJACENCY_GAP
    min_orthogonal_overlap_ratio: float = MIN_ORTHOGONAL_OVERLAP_RATIO
    scale_marker_coverage_ratio: float = 0.8
    nms_iou_threshold: float = NMS_IOU_THRESHOLD
    nms_containment_threshold: float = NMS_CONTAINMENT_THRESHOLD


DEFAULT_DETECTION_MERGE_OPTIONS: Final = DetectionMergeOptions()


def _area(detection: DetectionBox) -> float:
    return (detection.x1 - detection.x0) * (detection.y1 - detection.y0)


# 두 박스가 실제로 겹치거나, 가장자리가 가까운 거리 안에서 충분한 변 겹침을
# 공유하면 같은 물체로 본다. _union_detection_candidates에서 같은 프롬프트를
# 공유하는 후보끼리 병합 여부를 판단할 때 호출된다.
def _are_connected(
    left: DetectionBox, right: DetectionBox, options: DetectionMergeOptions
) -> bool:
    horizontal_overlap = min(left.x1, right.x1) - max(left.x0, right.x0)
    vertical_overlap = min(left.y1, right.y1) - max(left.y0, right.y0)
    if horizontal_overlap > 0 and vertical_overlap > 0:
        return True
    horizontal_gap = max(left.x0 - right.x1, right.x0 - left.x1, 0.0)
    vertical_gap = max(left.y0 - right.y1, right.y0 - left.y1, 0.0)
    minimum_height = min(left.y1 - left.y0, right.y1 - right.y0)
    minimum_width = min(left.x1 - left.x0, right.x1 - right.x0)
    minimum_vertical_overlap = minimum_height * options.min_orthogonal_overlap_ratio
    minimum_horizontal_overlap = minimum_width * options.min_orthogonal_overlap_ratio
    shares_enough_vertical_overlap = vertical_overlap >= minimum_vertical_overlap
    shares_enough_horizontal_overlap = horizontal_overlap >= minimum_horizontal_overlap
    is_horizontally_adjacent = (
        horizontal_gap <= options.edge_adjacency_gap and shares_enough_vertical_overlap
    )
    is_vertically_adjacent = (
        vertical_gap <= options.edge_adjacency_gap and shares_enough_horizontal_overlap
    )
    return is_horizontally_adjacent or is_vertically_adjacent


# 두 박스의 IoU(교집합/합집합)를 계산한다. _nms_detection_candidates에서
# 중복 판정에 쓰인다.
def _iou(left: DetectionBox, right: DetectionBox) -> float:
    intersection = _box_intersection_area(left, right)
    union = _area(left) + _area(right) - intersection
    if union <= 0.0:
        return 0.0
    return intersection / union


def _box_intersection_area(left: DetectionBox, right: DetectionBox) -> float:
    width = min(left.x1, right.x1) - max(left.x0, right.x0)
    height = min(left.y1, right.y1) - max(left.y0, right.y0)
    return max(width, 0.0) * max(height, 0.0)


# 더 작은 박스 기준으로 교집합이 차지하는 비율을 계산한다. 한 박스가 다른
# 박스에 거의 포함되는 경우(컨테인먼트)를 IoU와 별도로 잡아내기 위해
# _nms_detection_candidates에서 사용된다.
def _smaller_box_coverage(left: DetectionBox, right: DetectionBox) -> float:
    smaller_area = min(_area(left), _area(right))
    if smaller_area <= 0.0:
        return 0.0
    return _box_intersection_area(left, right) / smaller_area


def merge_detection_candidates(
    detections: tuple[DetectionBox, ...],
    image_size: tuple[int, int],
    scale_marker_bbox: BoundingBox | None = None,
    options: DetectionMergeOptions = DEFAULT_DETECTION_MERGE_OPTIONS,
) -> tuple[DetectionBox, ...]:
    """Deduplicate same-prompt boxes before SAM2 materialization."""
    # 호출부 시그니처를 안정적으로 유지하기 위해 받는 값들이며, 아래 두
    # 전략 중 어느 쪽도 이미지 크기나 스케일 마커 겹침으로 필터링하지 않는다.
    _ = image_size, scale_marker_bbox
    match options.strategy:
        case DetectionMergeStrategy.NMS:
            return _nms_detection_candidates(detections, options)
        case DetectionMergeStrategy.UNION:
            return _union_detection_candidates(detections, options)


# "union" 전략: 같은 프롬프트를 공유하며 연결된 박스들을 하나의 그룹으로
# 묶어 그 그룹을 감싸는 박스로 합친다. merge_detection_candidates에서
# strategy가 UNION일 때 호출된다.
def _union_detection_candidates(
    filtered_detections: tuple[DetectionBox, ...], options: DetectionMergeOptions
) -> tuple[DetectionBox, ...]:
    visited = [False] * len(filtered_detections)
    merged: list[DetectionBox] = []
    for start_index, start_detection in enumerate(filtered_detections):
        if visited[start_index]:
            continue
        visited[start_index] = True
        component = [start_detection]
        for member in component:
            for candidate_index, candidate in enumerate(filtered_detections):
                if visited[candidate_index]:
                    continue
                shares_prompt = (
                    candidate.prompt_text == member.prompt_text
                    and candidate.generated_prompt_id == member.generated_prompt_id
                )
                if shares_prompt and _are_connected(member, candidate, options):
                    visited[candidate_index] = True
                    component.append(candidate)
        highest_score = max(component, key=lambda candidate: candidate.score)
        merged.append(
            DetectionBox(
                x0=min(candidate.x0 for candidate in component),
                y0=min(candidate.y0 for candidate in component),
                x1=max(candidate.x1 for candidate in component),
                y1=max(candidate.y1 for candidate in component),
                score=highest_score.score,
                prompt_text=highest_score.prompt_text,
                generated_prompt_id=highest_score.generated_prompt_id,
            )
        )
    return tuple(
        sorted(
            merged,
            key=lambda candidate: (
                -candidate.score,
                candidate.prompt_text,
                candidate.generated_prompt_id,
                candidate.x0,
                candidate.y0,
                candidate.x1,
                candidate.y1,
            ),
        )
    )


# "nms" 전략: 점수가 높은 순으로 훑으며 IoU나 포함 비율이 임계값을 넘는
# 같은 프롬프트 중복 박스를 제거한다. merge_detection_candidates에서
# strategy가 NMS일 때 호출된다.
def _nms_detection_candidates(
    filtered_detections: tuple[DetectionBox, ...], options: DetectionMergeOptions
) -> tuple[DetectionBox, ...]:
    kept: list[DetectionBox] = []
    sorted_detections = sorted(
        filtered_detections, key=lambda detection: detection.score, reverse=True
    )
    for candidate in sorted_detections:
        is_duplicate = any(
            candidate.prompt_text == kept_candidate.prompt_text
            and candidate.generated_prompt_id == kept_candidate.generated_prompt_id
            and (
                _iou(candidate, kept_candidate) >= options.nms_iou_threshold
                or _smaller_box_coverage(candidate, kept_candidate)
                >= options.nms_containment_threshold
            )
            for kept_candidate in kept
        )
        if not is_duplicate:
            kept.append(candidate)
    return tuple(
        sorted(
            kept,
            key=lambda candidate: (
                -candidate.score,
                candidate.prompt_text,
                candidate.generated_prompt_id,
                candidate.x0,
                candidate.y0,
                candidate.x1,
                candidate.y1,
            ),
        )
    )
