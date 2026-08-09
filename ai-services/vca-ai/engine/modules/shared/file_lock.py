"""Cross-process exclusive lock for shared model-cache writers."""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING

from modules.shared.paths import (
    ensure_contained_write_path,
    ensure_no_symlink_path_components,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

_LOCK_PATH_UNSAFE_REASON = "cache lock path escapes or uses symlinks"


@contextmanager
def exclusive_file_lock(root: Path, lock_path: Path) -> Generator[None]:
    """Hold a blocking, OS-level lock scoped to lock_path for the block body.

    Serializes concurrent processes that share the same model cache root
    (for example two real pipeline runs) around one critical section, such
    as rebuilding and persisting a derived cache artifact. Not reentrant:
    acquiring the same lock twice from the same process will deadlock on
    POSIX and raise on Windows.
    """
    _ = ensure_no_symlink_path_components(lock_path, _LOCK_PATH_UNSAFE_REASON)
    _ = ensure_contained_write_path(root, lock_path, _LOCK_PATH_UNSAFE_REASON)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    try:
        _lock(file_descriptor)
        try:
            yield
        finally:
            _unlock(file_descriptor)
    finally:
        os.close(file_descriptor)


def _lock(file_descriptor: int) -> None:
    if sys.platform == "win32":
        import msvcrt  # noqa: PLC0415

        _ = os.lseek(file_descriptor, 0, os.SEEK_SET)
        msvcrt.locking(file_descriptor, msvcrt.LK_LOCK, 1)
    else:
        import fcntl  # noqa: PLC0415

        fcntl.flock(file_descriptor, fcntl.LOCK_EX)


def _unlock(file_descriptor: int) -> None:
    if sys.platform == "win32":
        import msvcrt  # noqa: PLC0415

        _ = os.lseek(file_descriptor, 0, os.SEEK_SET)
        msvcrt.locking(file_descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl  # noqa: PLC0415

        fcntl.flock(file_descriptor, fcntl.LOCK_UN)
