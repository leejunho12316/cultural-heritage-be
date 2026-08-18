from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from modules.preprocessing.model_runtime.inventory import ModelInventoryEntry
from modules.rough_masking.local_model.runner import (
    DETECTOR_MODEL_KEYS,
    EXPECTED_MODEL_REPO_IDS,
    EXPECTED_MODEL_REVISIONS,
    SAM2_MODEL_KEY,
    LocalModelCachePolicy,
    build_local_model_runner,
)
from modules.rough_masking.tests.artifacts.test_t4_generation import PNG_HEADER
from modules.rough_masking.tests.local_model.test_runner import model_entries
from modules.shared import ContractValidationError, DetectorLane

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


def snapshot_revision(files: Mapping[str, bytes]) -> str:
    snapshot_hash = hashlib.sha256()
    for relative_path, content in sorted(files.items()):
        snapshot_hash.update(relative_path.encode())
        snapshot_hash.update(b"\0")
        snapshot_hash.update(hashlib.sha256(content).hexdigest().encode())
        snapshot_hash.update(b"\n")
    return f"sha256:{snapshot_hash.hexdigest()}"


def test_local_model_runner_accepts_matching_model_content_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: detector and SAM2 directories whose content hashes match inventory.
    model_root = tmp_path / "models"
    entries = model_entries(model_root)
    detector_key = DETECTOR_MODEL_KEYS[DetectorLane.OWLV2_SAM2]
    detector_files = {"weights.bin": b"detector"}
    sam2_files = {"sam2_hiera_large.pt": b"sam2"}
    detector_path = entries[detector_key].local_dir / "weights.bin"
    sam2_path = entries[SAM2_MODEL_KEY].local_dir / "sam2_hiera_large.pt"
    _ = detector_path.write_bytes(detector_files["weights.bin"])
    _ = sam2_path.write_bytes(sam2_files["sam2_hiera_large.pt"])
    entries[detector_key] = ModelInventoryEntry(
        detector_key,
        EXPECTED_MODEL_REPO_IDS[detector_key],
        snapshot_revision(detector_files),
        entries[detector_key].local_dir,
    )
    entries[SAM2_MODEL_KEY] = ModelInventoryEntry(
        SAM2_MODEL_KEY,
        EXPECTED_MODEL_REPO_IDS[SAM2_MODEL_KEY],
        snapshot_revision(sam2_files),
        entries[SAM2_MODEL_KEY].local_dir,
    )
    monkeypatch.setitem(
        EXPECTED_MODEL_REVISIONS, detector_key, entries[detector_key].revision
    )
    monkeypatch.setitem(
        EXPECTED_MODEL_REVISIONS, SAM2_MODEL_KEY, entries[SAM2_MODEL_KEY].revision
    )
    image_path = tmp_path / "roi.png"
    _ = image_path.write_bytes(PNG_HEADER + b"roi")

    # When: runtime construction verifies model hashes at execution setup.
    runner = build_local_model_runner(
        lane=DetectorLane.OWLV2_SAM2,
        image_path=image_path,
        model_entries=entries,
        device="mps",
        model_cache_policy=LocalModelCachePolicy(model_root),
    )

    # Then: matching content hash permits local-only runtime construction.
    assert runner.lane is DetectorLane.OWLV2_SAM2


def test_local_model_runner_ignores_huggingface_cache_bookkeeping_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a detector directory whose pinned hash covers only the model
    # weights, plus a huggingface_hub bookkeeping file under ".cache" that
    # was not part of that pinned snapshot.
    model_root = tmp_path / "models"
    entries = model_entries(model_root)
    detector_key = DETECTOR_MODEL_KEYS[DetectorLane.OWLV2_SAM2]
    detector_files = {"weights.bin": b"detector"}
    sam2_files = {"sam2_hiera_large.pt": b"sam2"}
    detector_path = entries[detector_key].local_dir / "weights.bin"
    sam2_path = entries[SAM2_MODEL_KEY].local_dir / "sam2_hiera_large.pt"
    _ = detector_path.write_bytes(detector_files["weights.bin"])
    _ = sam2_path.write_bytes(sam2_files["sam2_hiera_large.pt"])
    entries[detector_key] = ModelInventoryEntry(
        detector_key,
        EXPECTED_MODEL_REPO_IDS[detector_key],
        snapshot_revision(detector_files),
        entries[detector_key].local_dir,
    )
    entries[SAM2_MODEL_KEY] = ModelInventoryEntry(
        SAM2_MODEL_KEY,
        EXPECTED_MODEL_REPO_IDS[SAM2_MODEL_KEY],
        snapshot_revision(sam2_files),
        entries[SAM2_MODEL_KEY].local_dir,
    )
    monkeypatch.setitem(
        EXPECTED_MODEL_REVISIONS, detector_key, entries[detector_key].revision
    )
    monkeypatch.setitem(
        EXPECTED_MODEL_REVISIONS, SAM2_MODEL_KEY, entries[SAM2_MODEL_KEY].revision
    )
    cache_bookkeeping_file = (
        entries[detector_key].local_dir / ".cache" / "huggingface" / "download"
        / "weights.bin.metadata"
    )
    cache_bookkeeping_file.parent.mkdir(parents=True)
    _ = cache_bookkeeping_file.write_bytes(b"etag-from-a-later-download-session")
    image_path = tmp_path / "roi.png"
    _ = image_path.write_bytes(PNG_HEADER + b"roi")

    # When: runtime construction verifies model hashes at execution setup.
    runner = build_local_model_runner(
        lane=DetectorLane.OWLV2_SAM2,
        image_path=image_path,
        model_entries=entries,
        device="mps",
        model_cache_policy=LocalModelCachePolicy(model_root),
    )

    # Then: the bookkeeping file outside the pinned snapshot is ignored.
    assert runner.lane is DetectorLane.OWLV2_SAM2


def test_local_model_runner_rejects_tampered_model_content(
    tmp_path: Path,
) -> None:
    # Given: trusted metadata but detector files whose bytes no longer match it.
    model_root = tmp_path / "models"
    entries = model_entries(model_root)
    detector_key = DETECTOR_MODEL_KEYS[DetectorLane.GROUNDED_SAM2]
    detector_file = entries[detector_key].local_dir / "model.safetensors"
    _ = detector_file.write_bytes(b"tampered")
    image_path = tmp_path / "roi.png"
    _ = image_path.write_bytes(PNG_HEADER + b"roi")

    # When / Then: local model weights cannot load after content tampering.
    with pytest.raises(ContractValidationError, match="content hash mismatch"):
        _ = build_local_model_runner(
            lane=DetectorLane.GROUNDED_SAM2,
            image_path=image_path,
            model_entries=entries,
            device="mps",
            model_cache_policy=LocalModelCachePolicy(model_root),
        )


def test_local_model_runner_rejects_model_file_symlink_escape(
    tmp_path: Path,
) -> None:
    # Given: a cache-contained directory with a file symlink to an outside target.
    model_root = tmp_path / "models"
    entries = model_entries(model_root)
    detector_key = DETECTOR_MODEL_KEYS[DetectorLane.GROUNDED_SAM2]
    outside_file = tmp_path / "outside.py"
    _ = outside_file.write_bytes(b"print('owned')")
    escaped_file = entries[detector_key].local_dir / "model.safetensors"
    escaped_file.symlink_to(outside_file)
    image_path = tmp_path / "roi.png"
    _ = image_path.write_bytes(PNG_HEADER + b"roi")

    # When / Then: resolved files must also stay under model_cache_root.
    with pytest.raises(ContractValidationError, match="escapes model cache root"):
        _ = build_local_model_runner(
            lane=DetectorLane.GROUNDED_SAM2,
            image_path=image_path,
            model_entries=entries,
            device="mps",
            model_cache_policy=LocalModelCachePolicy(model_root),
        )
