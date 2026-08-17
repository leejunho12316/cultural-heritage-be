from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest
from PIL import Image

from modules.preprocessing.views.ranking_hints import compute_object_ranking_hints

if TYPE_CHECKING:
    from pathlib import Path


def _write_image(path: Path, array: np.ndarray) -> None:
    Image.fromarray(array).save(path)


def test_uniform_crop_reads_near_zero_texture_and_color_variance(
    tmp_path: Path,
) -> None:
    # Given: a perfectly flat crop and a mask that fills its whole bbox.
    crop_path = tmp_path / "crop.png"
    mask_path = tmp_path / "mask.png"
    _write_image(crop_path, np.full((40, 40, 3), 128, dtype=np.uint8))
    _write_image(mask_path, np.full((40, 40), 255, dtype=np.uint8))

    # When: ranking hints are computed with a confident detection score.
    hints = compute_object_ranking_hints(crop_path, mask_path, detection_score=0.9)

    # Then: a flat crop has ~0 texture/color variance, and a full mask has
    # ~0 scarcity; uncertainty tracks 1 - score.
    assert hints.local_texture_variance == 0.0
    assert hints.local_color_variance == 0.0
    assert hints.candidate_scarcity == 0.0
    assert hints.candidate_uncertainty == pytest.approx(0.1)


def test_noisy_multicolor_crop_reads_higher_texture_and_color_variance(
    tmp_path: Path,
) -> None:
    # Given: a noisy, multi-color crop versus the flat crop above, and a
    # mask that only fills a quarter of its bbox.
    crop_path = tmp_path / "crop.png"
    mask_path = tmp_path / "mask.png"
    rng = np.random.default_rng(seed=7)
    noisy = rng.integers(0, 256, size=(40, 40, 3), dtype=np.uint8)
    _write_image(crop_path, noisy)
    sparse_mask = np.zeros((40, 40), dtype=np.uint8)
    sparse_mask[:20, :20] = 255
    _write_image(mask_path, sparse_mask)

    # When: ranking hints are computed with a low-confidence detection score.
    hints = compute_object_ranking_hints(crop_path, mask_path, detection_score=0.2)

    # Then: variance signals are meaningfully above zero, scarcity reflects
    # the sparse mask, and every value stays within the enforced [0, 1] range
    # (ObjectRankingHints itself raises if not).
    assert hints.local_texture_variance > 0.3
    assert hints.local_color_variance > 0.3
    assert hints.candidate_scarcity == pytest.approx(0.75)
    assert hints.candidate_uncertainty == pytest.approx(0.8)


def test_detection_score_outside_unit_range_is_clamped(tmp_path: Path) -> None:
    # Given: a flat crop/mask pair and an out-of-range detection score
    # (upstream detectors are not contractually guaranteed to stay in [0, 1]).
    crop_path = tmp_path / "crop.png"
    mask_path = tmp_path / "mask.png"
    _write_image(crop_path, np.full((10, 10, 3), 200, dtype=np.uint8))
    _write_image(mask_path, np.full((10, 10), 255, dtype=np.uint8))

    # When/Then: uncertainty clamps instead of producing an invalid hint.
    assert compute_object_ranking_hints(
        crop_path, mask_path, detection_score=1.5
    ).candidate_uncertainty == 0.0
    assert compute_object_ranking_hints(
        crop_path, mask_path, detection_score=-0.5
    ).candidate_uncertainty == 1.0
