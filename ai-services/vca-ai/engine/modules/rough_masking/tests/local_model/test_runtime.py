from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from modules.preprocessing.model_runtime.inventory import ModelInventoryEntry
from modules.rough_masking import ImageDimensions, build_roi_seed_request
from modules.rough_masking.local_model.inference import (
    bounded_detections,
    box_policy,
    suppress_near_duplicate_detections,
)
from modules.rough_masking.local_model.runtime import build_lane_runtime
from modules.rough_masking.local_model.segmentation import LocalDetection
from modules.rough_masking.tests.artifacts.test_t4_generation import (
    JPEG_HEADER,
    PNG_HEADER,
    anomaly_output,
    make_paths,
    make_roi_view,
)
from modules.shared import DetectorLane

if TYPE_CHECKING:
    from pathlib import Path

    from modules.rough_masking.artifacts.materialization import AnomalyMaskOutput
    from modules.rough_masking.contracts import AdapterRequest
    from modules.rough_masking.local_model.segmentation import LocalInferenceSettings


def test_detector_boxes_stay_in_roi_and_respect_per_prompt_cap(tmp_path: Path) -> None:
    # Given: oversized, valid, and lower-ranked detector boxes for one locked prompt.
    request = build_roi_seed_request(
        lane=DetectorLane.OWLV2_SAM2,
        view=make_roi_view(),
        image_dimensions=ImageDimensions(64, 48),
        paths=make_paths(tmp_path / "run", DetectorLane.OWLV2_SAM2),
    )

    # When: detector scores and boxes are normalized for materialization.
    detections = bounded_detections(
        scores=[0.9, 0.8, 0.7],
        boxes=[
            [-3.0, -4.0, 70.0, 53.0],
            [4.0, 5.0, 18.0, 20.0],
            [7.0, 8.0, 22.0, 24.0],
        ],
        policy=box_policy(request, request.prompts[0]),
    )

    # Then: the two highest boxes are ROI-local and retain the prompt provenance.
    assert [detection.bbox_xyxy for detection in detections] == [
        (0.0, 0.0, 64.0, 48.0),
        (4.0, 5.0, 18.0, 20.0),
    ]
    assert all(detection.prompt is request.prompts[0] for detection in detections)


def test_suppress_near_duplicate_detections_keeps_the_higher_score_box(
    tmp_path: Path,
) -> None:
    # Given: two detections whose boxes overlap almost completely (IoU well
    # above the NMS threshold) - the exact pattern a real mask_refining run
    # produced (two "accepted" candidates for the same physical spot, IoU
    # 0.9999) once the post-refinement dedup step it used to rely on was
    # removed. A third, genuinely separate detection stays untouched.
    request = build_roi_seed_request(
        lane=DetectorLane.OWLV2_SAM2,
        view=make_roi_view(),
        image_dimensions=ImageDimensions(200, 200),
        paths=make_paths(tmp_path / "run", DetectorLane.OWLV2_SAM2),
    )
    prompt = request.prompts[0]
    near_duplicate_high = LocalDetection(
        prompt=prompt, score=0.9, bbox_xyxy=(10.0, 10.0, 60.0, 60.0)
    )
    near_duplicate_low = LocalDetection(
        prompt=prompt, score=0.8, bbox_xyxy=(11.0, 11.0, 59.0, 59.0)
    )
    distinct = LocalDetection(
        prompt=prompt, score=0.7, bbox_xyxy=(150.0, 150.0, 180.0, 180.0)
    )

    # When: NMS runs over all three.
    kept = suppress_near_duplicate_detections(
        (near_duplicate_low, distinct, near_duplicate_high)
    )

    # Then: only the higher-score member of the overlapping pair survives,
    # alongside the untouched distinct detection.
    assert kept == (near_duplicate_high, distinct)


@pytest.mark.parametrize(
    ("lane", "seam_name"),
    [
        (DetectorLane.OWLV2_SAM2, "_detect_owlv2"),
        (DetectorLane.FLORENCE2_SAM2, "_detect_florence2"),
        (DetectorLane.GROUNDED_SAM2, "_detect_grounded"),
    ],
)
def test_lane_runtime_delegates_to_its_local_only_inference_seam(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane: DetectorLane,
    seam_name: str,
) -> None:
    # Given: a lane request and a fake local-only detector/SAM2 execution seam.
    request = build_roi_seed_request(
        lane=lane,
        view=make_roi_view(),
        image_dimensions=ImageDimensions(64, 48),
        paths=make_paths(tmp_path / "run", lane),
    )
    detector_entry = ModelInventoryEntry(
        f"{lane.value}.detector", "must-not-load/repo-id", "main", tmp_path / "detector"
    )
    sam2_entry = ModelInventoryEntry(
        "sam2.segmenter", "must-not-load/repo-id", "main", tmp_path / "sam2"
    )
    calls: list[tuple[Path, Path, Path, str, float, Path | None]] = []

    def fake_local_inference(
        request: AdapterRequest,
        image_path: Path,
        settings: LocalInferenceSettings,
    ) -> tuple[AnomalyMaskOutput, ...]:
        calls.append(
            (
                image_path,
                settings.detector_entry.local_dir,
                settings.sam2_entry.local_dir,
                settings.device,
                settings.max_mask_area_ratio,
                settings.object_mask_path,
            )
        )
        return (
            replace(
                anomaly_output(request),
                mask_png=PNG_HEADER + b"fake-sam2-mask",
                overlay_jpeg=JPEG_HEADER + b"fake-sam2-overlay",
            ),
        )

    monkeypatch.setattr(
        f"modules.rough_masking.local_model.runtime.{seam_name}",
        fake_local_inference,
        raising=False,
    )
    runtime = build_lane_runtime(
        lane=lane,
        detector_entry=detector_entry,
        sam2_entry=sam2_entry,
        device="mps",
    )
    image_path = tmp_path / "roi.png"

    # When: the adapter handles the ROI image.
    outputs = runtime.detect(request, image_path)

    # Then: it sends only local inventory paths through the injectable inference seam.
    assert calls == [
        (
            image_path,
            detector_entry.local_dir,
            sam2_entry.local_dir,
            "mps",
            request.threshold_config.max_mask_area_ratio,
            request.object_mask_path,
        )
    ]
    assert outputs[0].prompt is request.prompts[0]
