from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import pytest

from modules.preprocessing.model_runtime.inventory import ModelInventoryEntry
from modules.rough_masking import (
    ImageDimensions,
    build_roi_seed_request,
    execute_adapter,
)
from modules.rough_masking.local_model.runner import (
    DETECTOR_MODEL_KEYS,
    EXPECTED_MODEL_REPO_IDS,
    EXPECTED_MODEL_REVISIONS,
    SAM2_MODEL_KEY,
    LocalModelCachePolicy,
    build_local_model_runner,
)
from modules.rough_masking.tests.artifacts.test_t4_generation import (
    JPEG_HEADER,
    PNG_HEADER,
    anomaly_output,
    make_paths,
    make_roi_view,
)
from modules.shared import ContractValidationError, DetectorLane

if TYPE_CHECKING:
    from pathlib import Path

    from modules.rough_masking import AdapterRequest
    from modules.rough_masking.artifacts.materialization import AnomalyMaskOutput
    from modules.rough_masking.local_model.runtime import LaneRuntime


@dataclass(frozen=True, slots=True)
class CapturedRuntime:
    lane: DetectorLane
    detector_key: str
    sam2_key: str

    def detect(
        self, request: AdapterRequest, image_path: Path
    ) -> tuple[AnomalyMaskOutput, ...]:
        _ = image_path
        return (
            replace(
                anomaly_output(request),
                mask_png=PNG_HEADER + self.detector_key.encode(),
                overlay_jpeg=JPEG_HEADER + self.sam2_key.encode(),
            ),
        )


def model_entries(tmp_path: Path) -> dict[str, ModelInventoryEntry]:
    entries: dict[str, ModelInventoryEntry] = {}
    for key in (*DETECTOR_MODEL_KEYS.values(), SAM2_MODEL_KEY):
        local_dir = tmp_path / key.replace(".", "_")
        local_dir.mkdir(parents=True)
        entries[key] = ModelInventoryEntry(
            key,
            EXPECTED_MODEL_REPO_IDS[key],
            EXPECTED_MODEL_REVISIONS[key],
            local_dir,
        )
    return entries


@pytest.mark.parametrize(
    "lane",
    [
        DetectorLane.OWLV2_SAM2,
        DetectorLane.FLORENCE2_SAM2,
        DetectorLane.GROUNDED_SAM2,
    ],
)
def test_local_model_runner_routes_every_active_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lane: DetectorLane
) -> None:
    # Given: local inventory entries and a fake runtime builder for every lane.
    captured: list[tuple[DetectorLane, str, str, str]] = []

    def fake_build_lane_runtime(
        *,
        lane: DetectorLane,
        detector_entry: ModelInventoryEntry,
        sam2_entry: ModelInventoryEntry,
        device: str,
    ) -> LaneRuntime:
        captured.append((lane, detector_entry.key, sam2_entry.key, device))
        return CapturedRuntime(lane, detector_entry.key, sam2_entry.key)

    monkeypatch.setattr(
        "modules.rough_masking.local_model.runner.build_lane_runtime",
        fake_build_lane_runtime,
    )
    request = build_roi_seed_request(
        lane=lane,
        view=make_roi_view(),
        image_dimensions=ImageDimensions(64, 48),
        paths=make_paths(tmp_path / "run", lane),
    )
    image_path = tmp_path / "roi.png"
    _ = image_path.write_bytes(PNG_HEADER + b"roi")

    # When: the local model runner executes through the adapter seam.
    runner = build_local_model_runner(
        lane=lane,
        image_path=image_path,
        model_entries=model_entries(tmp_path / "models"),
        device="mps",
        model_cache_policy=LocalModelCachePolicy(
            tmp_path / "models", verify_hashes=False
        ),
    )
    receipt = execute_adapter(request, runner)

    # Then: the selected lane inventory emits a normalized anomaly rough mask.
    assert captured == [(lane, DETECTOR_MODEL_KEYS[lane], SAM2_MODEL_KEY, "mps")]
    assert receipt.candidates[0].lane is lane
    assert receipt.candidates[0].rough_mask.relative_path == "masks/anomaly-0000.png"
    assert receipt.diagnostics == ()


def test_local_model_runner_rejects_clipseg(tmp_path: Path) -> None:
    # Given: an excluded detector lane.
    image_path = tmp_path / "roi.png"
    _ = image_path.write_bytes(PNG_HEADER + b"roi")

    # When / Then: no local-model runner can be built for CLIPSeg.
    with pytest.raises(ContractValidationError, match="lane"):
        _ = build_local_model_runner(
            lane=DetectorLane.CLIPSEG,
            image_path=image_path,
            model_entries=model_entries(tmp_path / "models"),
            device="cpu",
            model_cache_policy=LocalModelCachePolicy(
                tmp_path / "models", verify_hashes=False
            ),
        )


def test_local_model_runner_rejects_missing_local_model_dir(tmp_path: Path) -> None:
    # Given: an inventory entry whose local directory is absent.
    entries = model_entries(tmp_path / "models")
    missing_key = DETECTOR_MODEL_KEYS[DetectorLane.OWLV2_SAM2]
    entries[missing_key] = ModelInventoryEntry(
        missing_key,
        EXPECTED_MODEL_REPO_IDS[missing_key],
        EXPECTED_MODEL_REVISIONS[missing_key],
        tmp_path / "missing",
    )
    image_path = tmp_path / "roi.png"
    _ = image_path.write_bytes(PNG_HEADER + b"roi")

    # When / Then: local-only execution rejects missing cache directories.
    with pytest.raises(ContractValidationError, match=missing_key):
        _ = build_local_model_runner(
            lane=DetectorLane.OWLV2_SAM2,
            image_path=image_path,
            model_entries=entries,
            device="mps",
            model_cache_policy=LocalModelCachePolicy(
                tmp_path / "models", verify_hashes=False
            ),
        )


def test_local_model_runner_accepts_cpu_runtime_device(tmp_path: Path) -> None:
    # Given: complete local inventory records and a valid ROI image.
    image_path = tmp_path / "roi.png"
    _ = image_path.write_bytes(PNG_HEADER + b"roi")

    # When: the local model runner is built for CPU-only execution.
    runner = build_local_model_runner(
        lane=DetectorLane.OWLV2_SAM2,
        image_path=image_path,
        model_entries=model_entries(tmp_path / "models"),
        device="cpu",
        model_cache_policy=LocalModelCachePolicy(
            tmp_path / "models", verify_hashes=False
        ),
    )

    # Then: construction accepts the CPU device without loading the models.
    assert runner.lane is DetectorLane.OWLV2_SAM2


def test_local_model_runner_rejects_untrusted_model_metadata(tmp_path: Path) -> None:
    # Given: a local inventory entry whose repository id differs from the trusted set.
    entries = model_entries(tmp_path / "models")
    bad_key = DETECTOR_MODEL_KEYS[DetectorLane.FLORENCE2_SAM2]
    good_entry = entries[bad_key]
    entries[bad_key] = ModelInventoryEntry(
        bad_key,
        "attacker/Florence-2-base",
        good_entry.revision,
        good_entry.local_dir,
    )
    image_path = tmp_path / "roi.png"
    _ = image_path.write_bytes(PNG_HEADER + b"roi")

    # When / Then: Florence remote-code loading refuses unexpected metadata.
    with pytest.raises(ContractValidationError, match="repo_id"):
        _ = build_local_model_runner(
            lane=DetectorLane.FLORENCE2_SAM2,
            image_path=image_path,
            model_entries=entries,
            device="mps",
            model_cache_policy=LocalModelCachePolicy(
                tmp_path / "models", verify_hashes=False
            ),
        )


def test_local_model_runner_rejects_untrusted_model_revision(tmp_path: Path) -> None:
    # Given: an inventory entry whose snapshot revision differs from the trusted set.
    entries = model_entries(tmp_path / "models")
    bad_key = DETECTOR_MODEL_KEYS[DetectorLane.FLORENCE2_SAM2]
    good_entry = entries[bad_key]
    entries[bad_key] = ModelInventoryEntry(
        bad_key,
        good_entry.repo_id,
        "main",
        good_entry.local_dir,
    )
    image_path = tmp_path / "roi.png"
    _ = image_path.write_bytes(PNG_HEADER + b"roi")

    # When / Then: mutable or unexpected revisions cannot reach trust_remote_code.
    with pytest.raises(ContractValidationError, match="revision"):
        _ = build_local_model_runner(
            lane=DetectorLane.FLORENCE2_SAM2,
            image_path=image_path,
            model_entries=entries,
            device="mps",
            model_cache_policy=LocalModelCachePolicy(
                tmp_path / "models", verify_hashes=False
            ),
        )


def test_local_model_runner_rejects_model_cache_symlink_escape(
    tmp_path: Path,
) -> None:
    # Given: a trusted-looking inventory entry whose local_dir symlink escapes root.
    model_root = tmp_path / "models"
    entries = model_entries(model_root)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    bad_key = DETECTOR_MODEL_KEYS[DetectorLane.FLORENCE2_SAM2]
    escaped_link = model_root / "escaped-florence"
    escaped_link.symlink_to(outside_dir, target_is_directory=True)
    good_entry = entries[bad_key]
    entries[bad_key] = ModelInventoryEntry(
        bad_key,
        good_entry.repo_id,
        good_entry.revision,
        escaped_link,
    )
    image_path = tmp_path / "roi.png"
    _ = image_path.write_bytes(PNG_HEADER + b"roi")

    # When / Then: resolved model directories must stay under model_cache_root.
    with pytest.raises(ContractValidationError, match="local_dir"):
        _ = build_local_model_runner(
            lane=DetectorLane.FLORENCE2_SAM2,
            image_path=image_path,
            model_entries=entries,
            device="mps",
            model_cache_policy=LocalModelCachePolicy(model_root, verify_hashes=False),
        )
