from __future__ import annotations

from modules.preprocessing.contracts.records import DetectionBox
from modules.preprocessing.contracts.views import BoundingBox
from modules.preprocessing.detection.merge import (
    DetectionMergeOptions,
    DetectionMergeStrategy,
    merge_detection_candidates,
)


def _detection(
    bbox: tuple[float, float, float, float],
    score: float,
    prompt: tuple[str, str] = ("artifact", "prompt-a"),
) -> DetectionBox:
    return DetectionBox(
        x0=bbox[0],
        y0=bbox[1],
        x1=bbox[2],
        y1=bbox[3],
        score=score,
        prompt_text=prompt[0],
        generated_prompt_id=prompt[1],
    )


UNION_OPTIONS = DetectionMergeOptions(strategy=DetectionMergeStrategy.UNION)


def test_merge_detection_candidates_unions_adjacent_same_prompt_boxes() -> None:
    # Given: same-prompt boxes separated only by a narrow horizontal gap.
    lower_score = _detection((10.0, 10.0, 30.0, 30.0), 0.6)
    higher_score = _detection((31.0, 12.0, 50.0, 28.0), 0.9)

    # When: detector candidates are merged before materialization.
    merged = merge_detection_candidates(
        (lower_score, higher_score), (100, 100), options=UNION_OPTIONS
    )

    # Then: their union keeps the highest score and shared prompt metadata.
    assert merged == (_detection((10.0, 10.0, 50.0, 30.0), 0.9),)


def test_merge_detection_candidates_keeps_separated_boxes_distinct() -> None:
    # Given: same-prompt boxes with a substantial gap between their edges.
    lower_score = _detection((0.0, 0.0, 10.0, 10.0), 0.3)
    higher_score = _detection((50.0, 0.0, 60.0, 10.0), 0.8)

    # When: detector candidates are merged before materialization.
    merged = merge_detection_candidates((lower_score, higher_score), (100, 100))

    # Then: both candidates remain and are sorted by score descending.
    assert merged == (higher_score, lower_score)


def test_merge_detection_candidates_connects_adjacent_boxes_transitively() -> None:
    # Given: a same-prompt chain where only neighboring boxes are adjacent.
    first = _detection((0.0, 0.0, 10.0, 10.0), 0.2)
    highest_score = _detection((11.0, 0.0, 21.0, 10.0), 0.9)
    third = _detection((22.0, 0.0, 32.0, 10.0), 0.5)

    # When: detector candidates are merged before materialization.
    merged = merge_detection_candidates(
        (first, highest_score, third), (100, 100), options=UNION_OPTIONS
    )

    # Then: the full connected component becomes one union candidate.
    assert merged == (_detection((0.0, 0.0, 32.0, 10.0), 0.9),)


def test_merge_detection_candidates_keeps_different_prompts_distinct() -> None:
    # Given: coincident boxes that do not share both prompt identity fields.
    prompt_a = _detection((0.0, 0.0, 10.0, 10.0), 0.9)
    different_id = _detection((0.0, 0.0, 10.0, 10.0), 0.8, ("artifact", "prompt-b"))
    different_text = _detection(
        (0.0, 0.0, 10.0, 10.0), 0.7, ("other artifact", "prompt-a")
    )

    # When: detector candidates are merged before materialization.
    merged = merge_detection_candidates(
        (prompt_a, different_id, different_text), (100, 100)
    )

    # Then: prompt identity boundaries prevent cross-prompt unions.
    assert merged == (prompt_a, different_id, different_text)


def test_merge_detection_candidates_unions_contained_same_prompt_boxes() -> None:
    # Given: a same-prompt detector box that contains a smaller candidate.
    container = _detection((0.0, 0.0, 100.0, 100.0), 0.95)
    contained = _detection((20.0, 20.0, 40.0, 40.0), 0.7)

    # When: detector candidates are merged before materialization.
    merged = merge_detection_candidates(
        (container, contained), (200, 200), options=UNION_OPTIONS
    )

    # Then: overlapping same-prompt boxes become one union candidate.
    assert merged == (container,)


def test_merge_detection_candidates_keeps_near_whole_image_box() -> None:
    # Given: a detector candidate covering at least 90% of a 100 by 100 input.
    near_whole_image = _detection((2.0, 2.0, 98.0, 97.0), 0.9)

    # When: candidates are prepared before SAM component splitting.
    merged = merge_detection_candidates((near_whole_image,), (100, 100))

    # Then: the broad container remains available for materialization.
    assert merged == (near_whole_image,)


def test_merge_detection_candidates_keeps_large_non_whole_image_box() -> None:
    # Given: a large candidate that does not span 95% of the input width.
    large_local_candidate = _detection((0.0, 0.0, 94.0, 100.0), 0.9)

    # When: candidates are filtered before connected-component traversal.
    merged = merge_detection_candidates((large_local_candidate,), (100, 100))

    # Then: the filter retains boxes that are not near-whole-image detections.
    assert merged == (large_local_candidate,)


def test_merge_detection_candidates_unions_nearby_fragment_boxes() -> None:
    # Given: same-prompt fragments with a 16px gap and 25% orthogonal overlap.
    left_fragment = _detection((10.0, 10.0, 30.0, 30.0), 0.6)
    right_fragment = _detection((46.0, 25.0, 66.0, 45.0), 0.9)

    # When: candidates are merged before materialization.
    merged = merge_detection_candidates(
        (left_fragment, right_fragment), (100, 100), options=UNION_OPTIONS
    )

    # Then: relaxed adjacency thresholds union the nearby fragments.
    assert merged == (_detection((10.0, 10.0, 66.0, 45.0), 0.9),)


def test_merge_detection_candidates_default_union_merges_overlapping_boxes() -> None:
    # Given: same-prompt detector boxes that overlap.
    left_object = _detection((0.0, 0.0, 50.0, 100.0), 0.9)
    right_object = _detection((45.0, 0.0, 95.0, 100.0), 0.8)

    # When: default union deduplication is applied before materialization.
    merged = merge_detection_candidates((left_object, right_object), (120, 120))

    # Then: overlapping boxes become one broad container for SAM splitting.
    assert merged == (_detection((0.0, 0.0, 95.0, 100.0), 0.9),)


def test_merge_detection_candidates_default_nms_suppresses_duplicate_boxes() -> None:
    # Given: two same-prompt boxes where the lower-score box nearly repeats the top hit.
    top_hit = _detection((0.0, 0.0, 100.0, 100.0), 0.9)
    duplicate = _detection((2.0, 2.0, 98.0, 98.0), 0.8)

    # When: NMS deduplication is explicitly requested before materialization.
    merged = merge_detection_candidates(
        (top_hit, duplicate),
        (120, 120),
        options=DetectionMergeOptions(strategy=DetectionMergeStrategy.NMS),
    )

    # Then: only the highest-score duplicate survives.
    assert merged == (top_hit,)


def test_merge_detection_candidates_keeps_scale_marker_overlapping_candidate() -> None:
    # Given: one valid large object candidate covers the detected scale marker area.
    scale_marker = BoundingBox(left=80.0, top=90.0, width=15.0, height=5.0)
    broad_object_candidate = _detection((0.0, 0.0, 100.0, 100.0), 0.8)
    object_candidate = _detection((10.0, 10.0, 30.0, 30.0), 0.7)

    # When: candidates are filtered before connected-component traversal.
    merged = merge_detection_candidates(
        (broad_object_candidate, object_candidate),
        (100, 100),
        scale_marker,
        DetectionMergeOptions(scale_marker_coverage_ratio=0.8),
    )

    # Then: scale marker overlap alone does not remove a valid object container.
    assert merged == (broad_object_candidate,)
