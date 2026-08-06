"""Startup fact to PostgreSQL storage record conversion."""

from __future__ import annotations

from uuid import uuid4

from modules.storage.contracts import StartupStorageSnapshot, StorageWriteRequest


def build_storage_write_request(
    snapshot: StartupStorageSnapshot,
) -> StorageWriteRequest:
    """Build one typed database snapshot from startup facts."""
    return StorageWriteRequest(
        assessment_run_id=uuid4(),
        artifact_id=snapshot.config.artifact_id,
        legacy_project_name=snapshot.project_name,
        status=snapshot.status,
        dry_run=snapshot.dry_run,
        requested_device=snapshot.requested_device,
        resolved_device=snapshot.resolved_device,
        current_stage=snapshot.current_stage,
        progress_percent=snapshot.progress_percent,
        config_json={
            "input_image_folder": snapshot.input_image_folder,
            "output_root": snapshot.output_root,
            "image_count": snapshot.image_count,
        },
    )
