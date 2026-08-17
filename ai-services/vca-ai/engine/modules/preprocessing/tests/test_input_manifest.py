from __future__ import annotations

from dataclasses import fields
from struct import pack
from typing import TYPE_CHECKING
from zlib import compress, crc32

from modules import preprocessing

if TYPE_CHECKING:
    from pathlib import Path


def _chunk(name: bytes, payload: bytes) -> bytes:
    return (
        pack(">I", len(payload))
        + name
        + payload
        + pack(">I", crc32(name + payload) & 0xFFFFFFFF)
    )


def _png_bytes(content: bytes = b"") -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", compress(b"\x00\x00\x00\x00\x00" + content))
        + _chunk(b"IEND", b"")
    )


PNG_BYTES = _png_bytes()


def _write_image(path: Path, content: bytes = PNG_BYTES) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(content)
    return path


def _valid_receipt(workspace: Path, run_root: str) -> preprocessing.PreflightReceipt:
    request = preprocessing.parse_run_request(
        ("inputs/artifact.png", "--run-root", run_root, "--dry-run"),
        workspace_root=workspace,
    )
    receipt = preprocessing.preflight_run(request)
    assert isinstance(receipt, preprocessing.PreflightReceipt)
    return receipt


def test_input_manifest_has_locked_fields_and_stable_receipt_hashes(
    tmp_path: Path,
) -> None:
    # Given: one valid input in a workspace and two different output roots.
    workspace = tmp_path / "workspace"
    _ = _write_image(workspace / "inputs" / "artifact.png")
    first_receipt = _valid_receipt(workspace, "runs/first")
    second_receipt = _valid_receipt(workspace, "runs/second")

    # When: manifests are computed without copying assets or inspecting dimensions.
    first = preprocessing.build_input_manifest(first_receipt)
    second = preprocessing.build_input_manifest(second_receipt)
    serialized = first.to_json()

    # Then: identity excludes output locations while the public record has all fields.
    assert first.manifest_id == second.manifest_id
    assert first.manifest_sha256 == second.manifest_sha256
    assert first.source_image_manifest_sha256 == second.source_image_manifest_sha256
    assert '"schema_version":"anomaly-input-manifest-v1"' in serialized
    assert '"asset_policy":"copy"' in serialized
    assert '"source_image_manifest_hash"' not in serialized
    assert {field.name for field in fields(first.images[0])} == {
        "image_id",
        "file_sha256",
        "original_path",
        "source_relative_path",
        "run_root_asset_path",
        "mime_type",
        "readability",
    }
    assert "width" not in serialized
    assert "height" not in serialized


def test_source_image_manifest_hash_changes_only_for_subset_content(
    tmp_path: Path,
) -> None:
    # Given: two content-distinct valid images in the same workspace.
    workspace = tmp_path / "workspace"
    _ = _write_image(workspace / "inputs" / "first.png", _png_bytes(b"one"))
    _ = _write_image(workspace / "inputs" / "second.png", _png_bytes(b"two"))
    first_request = preprocessing.parse_run_request(
        ("inputs/first.png", "--run-root", "runs/current"), workspace_root=workspace
    )
    subset_request = preprocessing.parse_run_request(
        ("inputs/second.png", "--run-root", "runs/current"), workspace_root=workspace
    )
    first_receipt = preprocessing.preflight_run(first_request)
    subset_receipt = preprocessing.preflight_run(subset_request)
    assert isinstance(first_receipt, preprocessing.PreflightReceipt)
    assert isinstance(subset_receipt, preprocessing.PreflightReceipt)

    # When: the same filename receives changed content after the first hash.
    first_manifest = preprocessing.build_input_manifest(first_receipt)
    _ = (workspace / "inputs" / "first.png").write_bytes(_png_bytes(b"changed"))
    changed_receipt = preprocessing.preflight_run(first_request)
    assert isinstance(changed_receipt, preprocessing.PreflightReceipt)
    changed_manifest = preprocessing.build_input_manifest(changed_receipt)
    subset_manifest = preprocessing.build_input_manifest(subset_receipt)

    # Then: source hashes react to subset/content, not run timestamp or output root.
    assert (
        first_manifest.source_image_manifest_sha256
        != subset_manifest.source_image_manifest_sha256
    )
    assert (
        first_manifest.source_image_manifest_sha256
        != changed_manifest.source_image_manifest_sha256
    )
    assert first_manifest.images[0].image_id != changed_manifest.images[0].image_id
