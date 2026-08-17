"""Deterministic input-manifest values without asset materialization."""

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path

from modules.preprocessing.preflight.checks import PreflightReceipt, Readability
from modules.preprocessing.preflight.request import AssetPolicy
from modules.shared import (
    INPUT_MANIFEST_SCHEMA_VERSION,
    ImageId,
    SourceImageManifestHash,
    SourceImageManifestItem,
    source_image_manifest_sha256,
)


@dataclass(frozen=True, slots=True)
class InputManifestImage:
    """One source asset record whose dimensions remain derived downstream metadata."""

    image_id: ImageId
    file_sha256: str
    original_path: str
    source_relative_path: str
    run_root_asset_path: str
    mime_type: str
    readability: Readability


@dataclass(frozen=True, slots=True)
class InputManifest:
    """Stable input receipt with source hashing isolated from runtime locations."""

    schema_version: str
    manifest_id: str
    manifest_sha256: str
    source_image_manifest_sha256: SourceImageManifestHash
    asset_policy: AssetPolicy
    images: tuple[InputManifestImage, ...]

    def to_json(self) -> str:
        """Serialize the manifest public record with locked field names."""
        return json.dumps(
            asdict(self), ensure_ascii=True, separators=(",", ":"), sort_keys=True
        )


def _file_sha256(source_path: Path) -> str:
    digest = sha256()
    with source_path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# 이 이미지의 image_id를 결정론적으로 만든다. build_input_manifest에서
# 이미지마다 호출된다.
def _image_id(relative_path: str, file_sha256: str) -> ImageId:
    # 경로와 내용 해시만으로 만들고 타임스탬프를 섞지 않으므로, 같은 입력으로
    # preprocessing을 재실행해도 같은 image_id가 나온다. 이후 모든 스테이지가
    # 이 값을 안정적인 조인 키로 사용한다.
    identity = f"{relative_path}\n{file_sha256}".encode()
    return ImageId(f"image-{sha256(identity).hexdigest()[:24]}")


# 정렬된 이미지 레코드와 asset_policy, 소스 이미지 해시를 정규 문자열로 이어
# 붙여 매니페스트 전체의 sha256을 계산한다. build_input_manifest에서 호출된다.
def _manifest_sha256(
    asset_policy: AssetPolicy,
    source_hash: SourceImageManifestHash,
    images: tuple[InputManifestImage, ...],
) -> str:
    records = tuple(
        (
            f"{image.image_id}|{image.file_sha256}|{image.source_relative_path}|"
            f"{image.mime_type}|{image.readability}"
        )
        for image in sorted(images, key=lambda item: item.source_relative_path)
    )
    canonical = "\n".join(
        (INPUT_MANIFEST_SCHEMA_VERSION, asset_policy, source_hash, *records)
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def build_input_manifest(receipt: PreflightReceipt) -> InputManifest:
    """Build a deterministic manifest from already validated source inputs."""
    images: list[InputManifestImage] = []
    source_items: list[SourceImageManifestItem] = []
    for source_image in receipt.images:
        file_sha256 = _file_sha256(source_image.source_path)
        image_id = _image_id(source_image.source_relative_path, file_sha256)
        suffix = source_image.source_path.suffix.lower()
        images.append(
            InputManifestImage(
                image_id=image_id,
                file_sha256=file_sha256,
                original_path=str(source_image.source_path),
                source_relative_path=source_image.source_relative_path,
                run_root_asset_path=str(
                    receipt.run_root / "assets" / "raw-inputs" / f"{image_id}{suffix}"
                ),
                mime_type=source_image.mime_type,
                readability=source_image.readability,
            )
        )
        source_items.append(
            SourceImageManifestItem(
                relative_image_path=source_image.source_relative_path,
                file_content_hash=file_sha256,
            )
        )
    ordered_images = tuple(sorted(images, key=lambda item: item.source_relative_path))
    source_hash = source_image_manifest_sha256(tuple(source_items))
    manifest_sha256 = _manifest_sha256(
        receipt.asset_policy, source_hash, ordered_images
    )
    return InputManifest(
        schema_version=INPUT_MANIFEST_SCHEMA_VERSION,
        manifest_id=f"input-manifest-{manifest_sha256[:24]}",
        manifest_sha256=manifest_sha256,
        source_image_manifest_sha256=source_hash,
        asset_policy=receipt.asset_policy,
        images=ordered_images,
    )
