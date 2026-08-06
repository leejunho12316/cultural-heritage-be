"""Typed contracts for the optional PostgreSQL startup storage mode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, override

if TYPE_CHECKING:
    from uuid import UUID

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class RdbStorageConfig:
    """Validated RDB connection settings selected at the CLI boundary."""

    artifact_id: UUID
    database_url: str


@dataclass(frozen=True, slots=True)
class StorageWriteRequest:
    """Complete transactional snapshot for one assessment run."""

    assessment_run_id: UUID
    artifact_id: UUID
    legacy_project_name: str
    status: str
    dry_run: bool
    requested_device: str
    resolved_device: str | None
    current_stage: str | None
    progress_percent: int
    config_json: JsonObject


@dataclass(frozen=True, slots=True)
class StartupStorageSnapshot:
    """Filesystem startup facts transformed into a storage write request."""

    config: RdbStorageConfig
    project_name: str
    input_image_folder: str
    output_root: str
    image_count: int
    dry_run: bool
    requested_device: str
    resolved_device: str | None
    status: str
    current_stage: str | None
    progress_percent: int


class RdbStorageWriter(Protocol):
    """Transactional persistence capability for one assessment-run snapshot."""

    def persist(self, request: StorageWriteRequest) -> None:
        """Commit one assessment-run snapshot."""
        ...


@dataclass(frozen=True, slots=True)
class StoragePersistenceError(RuntimeError):
    """Raised when an RDB dual-write cannot be committed."""

    message: str = "PostgreSQL assessment run persistence failed"

    @override
    def __str__(self) -> str:
        """Render the storage failure for a CLI boundary."""
        return self.message
