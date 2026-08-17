from __future__ import annotations

import pytest

from modules.prompt_generating import (
    BoundaryRelation,
    ColorBucket,
    CueMeasurements,
    Morphology,
    RgbColor,
    SizeClass,
    TextureProxy,
    extract_visual_cue,
)


@pytest.mark.parametrize(
    ("measurements", "color", "morphology", "texture", "size"),
    [
        (
            CueMeasurements(RgbColor(241, 239, 225), 0.02, 1.2, 0.0, 0.1, 0.8, 0.0),
            ColorBucket.WHITE,
            Morphology.CRUST,
            TextureProxy.POWDERY,
            SizeClass.LOCAL,
        ),
        (
            CueMeasurements(RgbColor(42, 165, 64), 0.01, 1.1, 0.0, 0.1, 0.4, 0.8),
            ColorBucket.GREEN,
            Morphology.SPOT,
            TextureProxy.ROUGH,
            SizeClass.LOCAL,
        ),
        (
            CueMeasurements(RgbColor(25, 24, 23), 0.35, 1.4, 0.0, 0.1, 0.1, 0.2),
            ColorBucket.BLACK,
            Morphology.BROAD_PATCH,
            TextureProxy.SMOOTH,
            SizeClass.BROAD,
        ),
        (
            CueMeasurements(RgbColor(110, 106, 96), 0.004, 12.0, 0.0, 0.1, 0.5, 0.1),
            ColorBucket.GRAY,
            Morphology.LINE,
            TextureProxy.ROUGH,
            SizeClass.MICRO,
        ),
        (
            CueMeasurements(RgbColor(40, 39, 37), 0.008, 1.0, 0.7, 0.1, 0.3, 0.1),
            ColorBucket.BLACK,
            Morphology.HOLE_PIT,
            TextureProxy.ROUGH,
            SizeClass.LOCAL,
        ),
        (
            CueMeasurements(RgbColor(145, 142, 134), 0.08, 1.6, 0.0, 0.8, 0.6, 0.4),
            ColorBucket.GRAY,
            Morphology.FLAKING_PATCH,
            TextureProxy.LAYERED,
            SizeClass.LOCAL,
        ),
    ],
)
def test_extract_visual_cue_for_synthetic_scalar_fixtures(
    measurements: CueMeasurements,
    color: ColorBucket,
    morphology: Morphology,
    texture: TextureProxy,
    size: SizeClass,
) -> None:
    # Given: a deterministic scalar representation of a candidate crop and mask.
    # When: visual cue metadata is extracted.
    cue = extract_visual_cue(measurements)

    # Then: all model-independent visual fields are stable and evidenced.
    assert cue.color_bucket is color
    assert cue.morphology is morphology
    assert cue.texture_proxy is texture
    assert cue.size_class is size
    assert cue.confidence > 0.0
    assert cue.reasons


def test_extract_visual_cue_marks_invalid_or_low_contrast_input_without_crashing(
) -> None:
    # Given: invalid mask area and an otherwise low-contrast scalar region.
    invalid = CueMeasurements(None, 0.0, 1.0, 0.0, 0.0, 0.0, None)
    low_contrast = CueMeasurements(
        RgbColor(122, 124, 123), 0.02, 1.0, 0.0, 0.1, 0.1, 0.0
    )

    # When: descriptors are extracted from both inputs.
    invalid_cue = extract_visual_cue(invalid)
    low_contrast_cue = extract_visual_cue(low_contrast)

    # Then: unavailable evidence is explicit rather than an image dependency failure.
    assert invalid_cue.color_bucket is ColorBucket.UNKNOWN
    assert invalid_cue.morphology is Morphology.UNKNOWN
    assert invalid_cue.confidence == 0.0
    assert "invalid mask area ratio" in invalid_cue.reasons
    assert low_contrast_cue.color_bucket is ColorBucket.GRAY
    assert low_contrast_cue.confidence < 1.0
    assert "low color contrast" in low_contrast_cue.reasons
    assert low_contrast_cue.boundary_relation is BoundaryRelation.INTERIOR


@pytest.mark.parametrize(
    ("boundary_overlap_ratio", "expected"),
    [
        (None, BoundaryRelation.UNKNOWN),
        (0.0, BoundaryRelation.INTERIOR),
        (0.2, BoundaryRelation.CROSSING),
        (0.5, BoundaryRelation.BOUNDARY),
    ],
)
def test_extract_visual_cue_locks_boundary_relation_thresholds(
    boundary_overlap_ratio: float | None, expected: BoundaryRelation
) -> None:
    # Given: fixed visual measurements with only boundary overlap changing.
    measurements = CueMeasurements(
        RgbColor(42, 165, 64), 0.01, 1.1, 0.0, 0.1, 0.4, boundary_overlap_ratio
    )

    # When: the cue is extracted.
    cue = extract_visual_cue(measurements)

    # Then: every boundary bucket remains locked.
    assert cue.boundary_relation is expected


def test_scalar_cue_extraction_reserves_crystalline_for_rag_vocabulary() -> None:
    # Given: current scalar fixtures cover every extractor texture branch.
    measurements = (
        CueMeasurements(RgbColor(145, 142, 134), 0.08, 1.6, 0.0, 0.8, 0.6, 0.4),
        CueMeasurements(RgbColor(241, 239, 225), 0.02, 1.2, 0.0, 0.1, 0.8, 0.0),
        CueMeasurements(RgbColor(42, 165, 64), 0.01, 1.1, 0.0, 0.1, 0.4, 0.8),
        CueMeasurements(RgbColor(25, 24, 23), 0.35, 1.4, 0.0, 0.1, 0.1, 0.2),
    )

    # When: scalar cues are extracted.
    textures = tuple(
        extract_visual_cue(measurement).texture_proxy for measurement in measurements
    )

    # Then: crystalline remains reserved RAG/card vocabulary, not extractor output.
    assert TextureProxy.CRYSTALLINE not in textures
