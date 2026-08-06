"""SQLAlchemy Core PostgreSQL writer for assessment-run snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from modules.storage.contracts import (
    RdbStorageConfig,
    StoragePersistenceError,
    StorageWriteRequest,
)
from modules.storage.schema import ARTIFACT, ASSESSMENT_RUN

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


@dataclass(frozen=True, slots=True)
class SqlAlchemyRdbStorageWriter:
    """Commit an assessment-run snapshot through one PostgreSQL transaction."""

    config: RdbStorageConfig

    def persist(self, request: StorageWriteRequest) -> None:
        """Write artifact and assessment-run snapshot atomically."""
        engine = create_engine(
            _psycopg_database_url(self.config.database_url), pool_pre_ping=True
        )
        try:
            with engine.begin() as connection:
                _write_snapshot(connection, request)
        except SQLAlchemyError as error:
            raise StoragePersistenceError from error
        finally:
            engine.dispose()


def _psycopg_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg://"):
        return database_url
    return database_url.replace("postgresql://", "postgresql+psycopg://", 1)


def _write_snapshot(connection: Connection, request: StorageWriteRequest) -> None:
    persisted_at = datetime.now(UTC)
    _ = connection.execute(
        insert(ARTIFACT)
        .values(
            id=request.artifact_id,
            metadata_json={"legacy_project_name": request.legacy_project_name},
            created_at=persisted_at,
        )
        .on_conflict_do_nothing(index_elements=(ARTIFACT.c.id,))
    )
    _ = connection.execute(
        select(ARTIFACT.c.id)
        .where(ARTIFACT.c.id == request.artifact_id)
        .with_for_update()
    )
    _ = connection.execute(
        insert(ASSESSMENT_RUN).values(
            id=request.assessment_run_id,
            artifact_id=request.artifact_id,
            run_number=select(
                func.coalesce(func.max(ASSESSMENT_RUN.c.run_number), 0) + 1
            )
            .where(ASSESSMENT_RUN.c.artifact_id == request.artifact_id)
            .scalar_subquery(),
            legacy_project_name=request.legacy_project_name,
            status=request.status,
            dry_run=request.dry_run,
            requested_device=request.requested_device,
            resolved_device=request.resolved_device,
            current_stage=request.current_stage,
            progress_percent=request.progress_percent,
            started_at=persisted_at,
            completed_at=completed_at_for_status(request.status, persisted_at),
            config_json=request.config_json,
        )
    )


def completed_at_for_status(
    status: str,
    persisted_at: datetime,
) -> datetime | None:
    """Return the persisted completion timestamp only for completed runs."""
    if status == "completed":
        return persisted_at
    return None
