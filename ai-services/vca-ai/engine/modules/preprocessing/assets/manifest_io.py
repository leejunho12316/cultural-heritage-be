"""Manifest, failure receipt, and asset hash IO for real preprocessing."""

from __future__ import annotations

import json
import sys
from hashlib import sha256
from typing import TYPE_CHECKING

from modules.preprocessing.contracts.records import (
    MaterializedAssetRecord,
    RealPreprocessingManifest,
)
from modules.shared import ContractValidationError, ExitCode

if TYPE_CHECKING:
    from pathlib import Path

    from modules.preprocessing.preflight.checks import PreflightFailureReceipt
    from modules.preprocessing.preflight.manifest import InputManifest


def write_preflight_failure(receipt: PreflightFailureReceipt, receipt_dir: Path) -> int:
    """Write a failed preflight receipt and return its exit code."""
    receipt_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "workspace_root": str(receipt.workspace_root),
        "run_root": str(receipt.run_root),
        "issues": [
            {"field": issue.field, "reason": issue.reason} for issue in receipt.issues
        ],
        "exit_code": int(receipt.exit_code),
    }
    _ = (receipt_dir / "preflight.json").write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n"
    )
    return int(receipt.exit_code)


def write_real_failure(run_root: Path, error: ContractValidationError) -> int:
    """Write a real preprocessing failure receipt and return its exit code."""
    receipt_dir = run_root / "receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    payload = {"field": error.field, "reason": error.reason, "exit_code": 2}
    _ = (receipt_dir / "real_preprocessing_failure.json").write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n"
    )
    return int(ExitCode.INCOMPLETE_OR_FAILURE)


def write_real_manifest(
    manifest_dir: Path, manifest: RealPreprocessingManifest
) -> None:
    """Write and print a real preprocessing manifest."""
    manifest_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manifest.to_jsonable(), ensure_ascii=False, indent=2)
    _ = (manifest_dir / "real_preprocessing_manifest.json").write_text(payload + "\n")
    _ = sys.stdout.write(payload + "\n")


def write_empty_real_manifest(
    manifest_dir: Path,
    manifest: InputManifest,
    inventory: str,
) -> None:
    """Write the zero-execution manifest used by dry runs."""
    real_manifest = RealPreprocessingManifest(
        schema_version="vca-real-preprocessing-v2",
        model_inventory=inventory,
        device="not_executed_dry_run",
        detector_lane_status="dry_run_not_executed",
        model_invocations=0,
        sam2_calls=0,
        manifest_id=manifest.manifest_id,
        processed_image_count=0,
        object_count=0,
        images=(),
        objects=(),
    )
    write_real_manifest(manifest_dir, real_manifest)


def detector_input_record(path: Path) -> MaterializedAssetRecord:
    """Build an integrity record for a prepared detector image."""
    resolved = path.resolve()
    return MaterializedAssetRecord(
        path=str(resolved),
        sha256=hash_file(resolved),
        media_type="image/jpeg",
    )


def hash_file(path: Path) -> str:
    """Return the SHA-256 digest of a file."""
    digest = sha256()
    with path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
