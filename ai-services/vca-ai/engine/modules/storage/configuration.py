"""CLI-boundary parsing for optional PostgreSQL storage settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from modules.shared import ContractValidationError
from modules.storage.contracts import RdbStorageConfig

if TYPE_CHECKING:
    from collections.abc import Mapping

DATABASE_URL_ENVIRONMENT_VARIABLE = "VCA_DATABASE_URL"


@dataclass(frozen=True, slots=True)
class RdbStorageCliArguments:
    """Raw RDB values accepted at the startup CLI boundary."""

    artifact_id_value: str | None
    database_url_value: str | None


def rdb_storage_config(
    arguments: RdbStorageCliArguments,
    environment: Mapping[str, str],
) -> RdbStorageConfig:
    """Parse RDB-only CLI settings into a validated immutable configuration."""
    return RdbStorageConfig(
        artifact_id=_artifact_id(arguments.artifact_id_value),
        database_url=_database_url(arguments.database_url_value, environment),
    )


def _artifact_id(value: str | None) -> UUID:
    if value is None:
        field = "artifact_id"
        reason = "is required in rdb storage mode"
        raise ContractValidationError(field, reason)
    try:
        return UUID(value)
    except ValueError as error:
        field = "artifact_id"
        reason = "must be a UUID"
        raise ContractValidationError(field, reason) from error


def _database_url(value: str | None, environment: Mapping[str, str]) -> str:
    candidate = (
        value
        if value is not None
        else environment.get(DATABASE_URL_ENVIRONMENT_VARIABLE)
    )
    if candidate is None or not candidate.strip():
        field = "db_url"
        reason = f"is required or set {DATABASE_URL_ENVIRONMENT_VARIABLE}"
        raise ContractValidationError(field, reason)
    database_url = candidate.strip()
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        field = "db_url"
        reason = "must be a PostgreSQL URL"
        raise ContractValidationError(field, reason)
    return database_url
