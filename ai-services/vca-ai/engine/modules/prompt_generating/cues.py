"""Pure scalar extraction of visual cue descriptors."""

from .models import (
    BoundaryRelation,
    ColorBucket,
    CueMeasurements,
    Morphology,
    SizeClass,
    TextureProxy,
    VisualCue,
)

_WHITE_MINIMUM = 210
_WHITE_MAXIMUM_SPREAD = 45
_BLACK_MAXIMUM = 60
_YELLOW_RED_MINIMUM = 140
_YELLOW_GREEN_MINIMUM = 120
_GRAY_MAXIMUM_SPREAD = 25
_HOLE_VOID_RATIO = 0.5
_LINE_ASPECT_RATIO = 5.0
_FLAKING_FRAGMENTATION = 0.6
_BROAD_AREA_RATIO = 0.2
_POWDERY_TEXTURE_VARIANCE = 0.7
_ROUGH_TEXTURE_VARIANCE = 0.25
_MICRO_AREA_RATIO = 0.005
_LOCAL_AREA_RATIO = 0.1
_INTERIOR_BOUNDARY_OVERLAP = 0.05
_BOUNDARY_OVERLAP = 0.5
_LOW_COLOR_CONTRAST = 10


def _color_bucket(measurements: CueMeasurements) -> ColorBucket:
    """Map scalar RGB values into the closed color vocabulary."""
    rgb = measurements.dominant_rgb
    if rgb is None:
        return ColorBucket.UNKNOWN
    spread = max(rgb.red, rgb.green, rgb.blue) - min(rgb.red, rgb.green, rgb.blue)
    color = ColorBucket.UNKNOWN
    if (
        min(rgb.red, rgb.green, rgb.blue) >= _WHITE_MINIMUM
        and spread <= _WHITE_MAXIMUM_SPREAD
    ):
        color = ColorBucket.WHITE
    elif max(rgb.red, rgb.green, rgb.blue) <= _BLACK_MAXIMUM:
        color = ColorBucket.BLACK
    elif rgb.green >= rgb.red * 1.2 and rgb.green >= rgb.blue * 1.2:
        color = ColorBucket.GREEN
    elif rgb.red >= rgb.green * 1.25 and rgb.red >= rgb.blue * 1.25:
        color = ColorBucket.REDDISH
    elif (
        rgb.red >= _YELLOW_RED_MINIMUM
        and rgb.green >= _YELLOW_GREEN_MINIMUM
        and rgb.blue * 1.3 <= rgb.green
    ):
        color = ColorBucket.YELLOW
    elif spread <= _GRAY_MAXIMUM_SPREAD:
        color = ColorBucket.GRAY
    return color


def _morphology(measurements: CueMeasurements, color: ColorBucket) -> Morphology:
    """Classify deterministic scalar shape features into morphology buckets."""
    if measurements.void_ratio >= _HOLE_VOID_RATIO:
        return Morphology.HOLE_PIT
    if measurements.aspect_ratio >= _LINE_ASPECT_RATIO:
        return Morphology.LINE
    if measurements.fragmentation >= _FLAKING_FRAGMENTATION:
        return Morphology.FLAKING_PATCH
    if measurements.mask_area_ratio > _BROAD_AREA_RATIO:
        return Morphology.BROAD_PATCH
    if (
        color is ColorBucket.WHITE
        and measurements.texture_variance >= _POWDERY_TEXTURE_VARIANCE
    ):
        return Morphology.CRUST
    return Morphology.SPOT


def _texture(measurements: CueMeasurements) -> TextureProxy:
    """Classify scalar texture and fragmentation signals into a proxy."""
    if measurements.fragmentation >= _FLAKING_FRAGMENTATION:
        return TextureProxy.LAYERED
    if measurements.texture_variance >= _POWDERY_TEXTURE_VARIANCE:
        return TextureProxy.POWDERY
    if measurements.texture_variance >= _ROUGH_TEXTURE_VARIANCE:
        return TextureProxy.ROUGH
    return TextureProxy.SMOOTH


def _size(mask_area_ratio: float) -> SizeClass:
    """Classify a valid normalized mask area into a coarse size class."""
    if mask_area_ratio <= _MICRO_AREA_RATIO:
        return SizeClass.MICRO
    if mask_area_ratio <= _LOCAL_AREA_RATIO:
        return SizeClass.LOCAL
    return SizeClass.BROAD


def _boundary_relation(boundary_overlap_ratio: float | None) -> BoundaryRelation:
    """Classify a valid object-boundary overlap ratio."""
    if boundary_overlap_ratio is None:
        return BoundaryRelation.UNKNOWN
    if boundary_overlap_ratio <= _INTERIOR_BOUNDARY_OVERLAP:
        return BoundaryRelation.INTERIOR
    if boundary_overlap_ratio >= _BOUNDARY_OVERLAP:
        return BoundaryRelation.BOUNDARY
    return BoundaryRelation.CROSSING


def extract_visual_cue(measurements: CueMeasurements) -> VisualCue:
    """Extract deterministic cue metadata without loading images or masks."""
    if not 0.0 < measurements.mask_area_ratio <= 1.0:
        return VisualCue(
            color_bucket=ColorBucket.UNKNOWN,
            morphology=Morphology.UNKNOWN,
            texture_proxy=TextureProxy.UNKNOWN,
            size_class=SizeClass.UNKNOWN,
            boundary_relation=BoundaryRelation.UNKNOWN,
            confidence=0.0,
            reasons=("invalid mask area ratio",),
        )
    color = _color_bucket(measurements)
    morphology = _morphology(measurements, color)
    contrast = 0
    if measurements.dominant_rgb is not None:
        rgb = measurements.dominant_rgb
        contrast = max(rgb.red, rgb.green, rgb.blue) - min(rgb.red, rgb.green, rgb.blue)
    reasons = [f"color: {color.value}", f"morphology: {morphology.value}"]
    confidence = 0.95
    if contrast < _LOW_COLOR_CONTRAST:
        reasons.append("low color contrast")
        confidence = 0.65
    return VisualCue(
        color_bucket=color,
        morphology=morphology,
        texture_proxy=_texture(measurements),
        size_class=_size(measurements.mask_area_ratio),
        boundary_relation=_boundary_relation(measurements.boundary_overlap_ratio),
        confidence=confidence,
        reasons=tuple(reasons),
    )
