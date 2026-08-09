from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from modules.storage import (
    RdbStorageConfig,
    StartupStorageSnapshot,
    build_storage_write_request,
)
from modules.storage.schema import (
    ASSESSMENT_REPORT,
    ASSESSMENT_RUN,
    METADATA,
    UPLOADED_IMAGE,
)
from modules.storage.writer import completed_at_for_status


def test_build_storage_write_request_keeps_only_fe_startup_snapshot() -> None:
    # Given: a completed startup run with filesystem-only stage receipts.
    config = RdbStorageConfig(
        artifact_id=UUID("12345678-1234-5678-1234-567812345678"),
        database_url="postgresql://vca:secret@db/vca",
    )

    # When: storage records are derived from the startup snapshot.
    snapshot = StartupStorageSnapshot(
        config=config,
        project_name="demo",
        input_image_folder="/workspace/input",
        output_root="/workspace/output/result/demo",
        image_count=2,
        dry_run=False,
        requested_device="auto",
        resolved_device="mps",
        status="completed",
        current_stage=None,
        progress_percent=100,
    )
    write_request = build_storage_write_request(snapshot)

    # Then: only the FE-visible startup state is retained.
    assert isinstance(write_request.assessment_run_id, UUID)
    assert write_request.artifact_id == config.artifact_id
    assert write_request.legacy_project_name == "demo"
    assert write_request.status == "completed"
    assert write_request.dry_run is False
    assert write_request.requested_device == "auto"
    assert write_request.resolved_device == "mps"
    assert write_request.current_stage is None
    assert write_request.progress_percent == 100
    assert write_request.config_json == {
        "input_image_folder": "/workspace/input",
        "output_root": "/workspace/output/result/demo",
        "image_count": 2,
    }
    assert not hasattr(write_request, "stage_runs")


def test_core_schema_keeps_fe_and_report_tables_without_stage_receipts() -> None:
    # Given: the PostgreSQL Core metadata used by optional persistence.
    table_names = set(METADATA.tables)

    # When/Then: it has FE/report tables but no stage receipt persistence tables.
    assert table_names == {
        "artifact",
        "assessment_run",
        "uploaded_image",
        "assessment_report",
        "report_pdf_job",
    }
    assert set(ASSESSMENT_RUN.c.keys()) == {
        "id",
        "artifact_id",
        "run_number",
        "legacy_project_name",
        "status",
        "dry_run",
        "requested_device",
        "resolved_device",
        "current_stage",
        "progress_percent",
        "started_at",
        "completed_at",
        "config_json",
    }
    assert set(UPLOADED_IMAGE.c.keys()) == {
        "id",
        "artifact_id",
        "filename",
        "object_key",
        "thumbnail_object_key",
        "media_type",
        "content_sha256",
        "size_bytes",
        "status",
        "width",
        "height",
        "display_order",
        "created_at",
        "uploaded_at",
    }
    assert set(ASSESSMENT_REPORT.c.keys()) == {
        "assessment_run_id",
        "report_json",
        "status",
        "overall_condition",
        "risk_level",
        "generated_at",
        "created_at",
        "updated_at",
    }


def test_schema_ddl_keeps_fe_and_report_tables_without_stage_receipts() -> None:
    # Given: the PostgreSQL DDL for optional persistence.
    schema_path = Path(__file__).with_name("schema.sql")

    # When: table declarations are inspected.
    ddl = schema_path.read_text(encoding="utf-8")

    # Then: it matches the Core FE/report persistence boundary.
    for table_name in METADATA.tables:
        assert f"CREATE TABLE {table_name} (" in ddl
    for column_name in ("content_sha256", "size_bytes", "uploaded_at"):
        assert column_name in ddl
    assert "assessment_run_id uuid PRIMARY KEY REFERENCES assessment_run(id)" in ddl
    assert "report_json jsonb NOT NULL" in ddl
    assert "CREATE TABLE report_summary (" not in ddl
    assert "CREATE TABLE report_finding (" not in ddl
    assert "CREATE TABLE report_mask (" not in ddl
    assert "CREATE TABLE report_recommendation (" not in ddl
    assert "CREATE TABLE report_evidence (" not in ddl
    assert "CREATE TABLE report_pdf_job (" in ddl
    assert "stage_run" not in ddl
    assert "stage_output" not in ddl


def test_docs_explain_runtime_writer_scope_and_target_erd_tables() -> None:
    # Given: the public docs that describe optional RDB persistence.
    project_root = Path(__file__).resolve().parents[2]
    readme = (project_root / "README.md").read_text(encoding="utf-8")
    contract = (project_root / "docs" / "vca-api-contract.md").read_text(
        encoding="utf-8",
    )

    # When/Then: they separate current startup writes from target ERD tables.
    for content in (readme, contract):
        assert "artifact + assessment_run" in content
        assert "uploaded_image" in content
        assert "assessment_report" in content
        assert "report_json" in content
        assert "report_pdf_job" in content
        assert "startup writer" in content
        assert "does not populate" in content


def test_writer_completed_at_is_only_set_for_completed_runs() -> None:
    # Given: one persisted timestamp and terminal/non-terminal run statuses.
    persisted_at = datetime(2026, 8, 4, tzinfo=UTC)

    # When/Then: only successful completed runs receive completed_at.
    assert completed_at_for_status("completed", persisted_at) == persisted_at
    assert completed_at_for_status("failed", persisted_at) is None
    assert completed_at_for_status("skipped", persisted_at) is None
