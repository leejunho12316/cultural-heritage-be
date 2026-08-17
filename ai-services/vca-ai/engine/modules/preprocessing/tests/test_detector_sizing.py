from __future__ import annotations

from modules.preprocessing.contracts.records import detector_input_size_for_image


def test_detector_input_size_for_image_uses_long_side_buckets() -> None:
    # Given: representative detector input dimensions from small and large images.
    small = (1200, 900)
    image10 = (4900, 3675)
    largest_smoke = (5895, 4421)

    # When: detector input size is resolved from each image size.
    resolved = tuple(
        detector_input_size_for_image(width, height)
        for width, height in (small, image10, largest_smoke)
    )

    # Then: sizing follows the patch-safe long-side buckets.
    assert resolved == (960, 1152, 1344)
