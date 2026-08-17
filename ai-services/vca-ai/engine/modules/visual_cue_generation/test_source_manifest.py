from __future__ import annotations

import json
from hashlib import sha256
from typing import TYPE_CHECKING

from modules.shared import ImageId
from modules.visual_cue_generation.source_manifest import source_assets_by_image

if TYPE_CHECKING:
    from pathlib import Path


def test_source_assets_by_image_reads_preprocessing_input_manifest(
    tmp_path: Path,
) -> None:
    # Given: preprocessing input manifest rows with run-root image assets.
    image_path = tmp_path / "assets" / "image-001.jpg"
    image_bytes = b"\xff\xd8image"
    image_path.parent.mkdir(parents=True)
    _ = image_path.write_bytes(image_bytes)
    manifest = tmp_path / "input_manifest.json"
    _ = manifest.write_text(
        json.dumps(
            {
                "images": [
                    {
                        "file_sha256": sha256(image_bytes).hexdigest(),
                        "image_id": "image-001",
                        "mime_type": "image/jpeg",
                        "run_root_asset_path": str(image_path),
                    }
                ]
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    # When: the manifest is parsed for Qwen source assets.
    assets = source_assets_by_image(manifest, tmp_path)

    # Then: assets are keyed by image ID with contained relative paths.
    assert assets[ImageId("image-001")].relative_path == "assets/image-001.jpg"
    assert assets[ImageId("image-001")].media_type == "image/jpeg"
