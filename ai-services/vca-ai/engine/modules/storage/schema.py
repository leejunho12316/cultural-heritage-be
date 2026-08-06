"""SQLAlchemy Core table declarations for the RDB storage MVP."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as POSTGRESQL_UUID

METADATA = MetaData()

ARTIFACT = Table(
    "artifact",
    METADATA,
    Column("id", POSTGRESQL_UUID(as_uuid=True), primary_key=True),
    Column("artifact_code", Text, unique=True),
    Column("title", Text),
    Column("description", Text),
    Column("representative_image_key", Text),
    Column("metadata_json", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

ASSESSMENT_RUN = Table(
    "assessment_run",
    METADATA,
    Column("id", POSTGRESQL_UUID(as_uuid=True), primary_key=True),
    Column(
        "artifact_id",
        POSTGRESQL_UUID(as_uuid=True),
        ForeignKey("artifact.id"),
        nullable=False,
    ),
    Column("run_number", Integer, nullable=False),
    Column("legacy_project_name", Text),
    Column("status", Text, nullable=False),
    Column("dry_run", Boolean, nullable=False),
    Column("requested_device", Text),
    Column("resolved_device", Text),
    Column("current_stage", Text),
    Column("progress_percent", Integer, nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("completed_at", DateTime(timezone=True)),
    Column("config_json", JSONB, nullable=False),
    UniqueConstraint("artifact_id", "run_number"),
)

UPLOADED_IMAGE = Table(
    "uploaded_image",
    METADATA,
    Column("id", POSTGRESQL_UUID(as_uuid=True), primary_key=True),
    Column(
        "artifact_id",
        POSTGRESQL_UUID(as_uuid=True),
        ForeignKey("artifact.id"),
        nullable=False,
    ),
    Column("filename", Text, nullable=False),
    Column("object_key", Text, nullable=False),
    Column("thumbnail_object_key", Text),
    Column("media_type", Text),
    Column("content_sha256", Text),
    Column("size_bytes", BigInteger),
    Column("status", Text, nullable=False),
    Column("width", Integer),
    Column("height", Integer),
    Column("display_order", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("uploaded_at", DateTime(timezone=True)),
    UniqueConstraint("artifact_id", "object_key"),
)

ASSESSMENT_REPORT = Table(
    "assessment_report",
    METADATA,
    Column(
        "assessment_run_id",
        POSTGRESQL_UUID(as_uuid=True),
        ForeignKey("assessment_run.id"),
        primary_key=True,
        nullable=False,
    ),
    Column("report_json", JSONB, nullable=False),
    Column("status", Text),
    Column("overall_condition", Text),
    Column("risk_level", Text),
    Column("generated_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

REPORT_PDF_JOB = Table(
    "report_pdf_job",
    METADATA,
    Column("id", POSTGRESQL_UUID(as_uuid=True), primary_key=True),
    Column(
        "assessment_run_id",
        POSTGRESQL_UUID(as_uuid=True),
        ForeignKey("assessment_run.id"),
        nullable=False,
    ),
    Column("status", Text, nullable=False),
    Column("layout", Text, nullable=False),
    Column("pdf_object_key", Text),
    Column("requested_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
)
