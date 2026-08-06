"""Contained detector output assets with content identity."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from modules.rough_masking.artifacts.records import JsonValue


@dataclass(frozen=True, slots=True)
class AssetReference:
    """Contained output asset with content identity."""

    relative_path: str
    sha256: str
    media_type: str


def normalize_asset(
    root: Path, raw: JsonValue | None, field: str
) -> tuple[AssetReference | None, str | None]:
    """Validate an asset path, media type, and content identity."""
    if (
        not isinstance(raw, str)
        or not raw
        or Path(raw).is_absolute()
        or ".." in Path(raw).parts
    ):
        return None, f"{field}_path_escape"
    path = (root / raw).resolve()
    if not path.is_relative_to(root.resolve()):
        return None, f"{field}_path_escape"
    if not path.is_file():
        return None, f"{field}_file_missing"
    suffix = path.suffix.lower()
    if (field == "mask" and suffix != ".png") or (
        field == "overlay" and suffix not in {".jpg", ".jpeg"}
    ):
        return None, f"{field}_media_type_invalid"
    try:
        digest = sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None, f"{field}_file_unreadable"
    media_type = "image/png" if suffix == ".png" else "image/jpeg"
    return AssetReference(raw, digest, media_type), None
