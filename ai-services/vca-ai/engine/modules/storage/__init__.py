"""PostgreSQL persistence contracts for optional orchestration dual writes."""

from modules.storage.configuration import RdbStorageCliArguments, rdb_storage_config
from modules.storage.contracts import (
    RdbStorageConfig,
    RdbStorageWriter,
    StartupStorageSnapshot,
    StoragePersistenceError,
    StorageWriteRequest,
)
from modules.storage.records import build_storage_write_request
from modules.storage.writer import SqlAlchemyRdbStorageWriter

__all__ = (
    "RdbStorageCliArguments",
    "RdbStorageConfig",
    "RdbStorageWriter",
    "SqlAlchemyRdbStorageWriter",
    "StartupStorageSnapshot",
    "StoragePersistenceError",
    "StorageWriteRequest",
    "build_storage_write_request",
    "rdb_storage_config",
)
