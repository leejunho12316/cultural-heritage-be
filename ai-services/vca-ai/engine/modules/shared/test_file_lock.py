from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import pytest

from modules.shared.errors import PathSafetyError
from modules.shared.file_lock import exclusive_file_lock

if TYPE_CHECKING:
    from pathlib import Path


def test_exclusive_file_lock_runs_body_and_is_reusable(tmp_path: Path) -> None:
    # Given: a lock path under a fresh model cache root.
    root = tmp_path / "models"
    lock_path = root / "rag" / ".materialize.lock"
    entered = False

    # When: the lock is acquired, used, and released.
    with exclusive_file_lock(root, lock_path):
        entered = True

    # Then: the block ran and a later acquisition still succeeds.
    assert entered
    assert lock_path.is_file()
    with exclusive_file_lock(root, lock_path):
        pass


def test_exclusive_file_lock_serializes_concurrent_holders(tmp_path: Path) -> None:
    # Given: several threads that each try to hold the same lock at once.
    root = tmp_path / "models"
    lock_path = root / "rag" / ".materialize.lock"
    active_holders: list[int] = []
    max_concurrent_holders = [0]
    holders_guard = threading.Lock()

    def hold_lock() -> None:
        with exclusive_file_lock(root, lock_path):
            with holders_guard:
                active_holders.append(1)
                max_concurrent_holders[0] = max(
                    max_concurrent_holders[0], len(active_holders)
                )
            time.sleep(0.05)
            with holders_guard:
                _ = active_holders.pop()

    threads = [threading.Thread(target=hold_lock) for _ in range(5)]

    # When: all threads race to acquire the lock concurrently.
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Then: at most one thread was ever inside the critical section at once.
    assert max_concurrent_holders[0] == 1


def test_exclusive_file_lock_rejects_symlinked_lock_path(tmp_path: Path) -> None:
    # Given: the lock path is a symlink to a file outside the cache root.
    root = tmp_path / "models"
    root.mkdir()
    external = tmp_path / "external.lock"
    _ = external.write_text("sentinel", encoding="utf-8")
    lock_path = root / "materialize.lock"
    lock_path.symlink_to(external)

    # When/Then: acquiring the lock fails before following the symlink.
    with pytest.raises(PathSafetyError), exclusive_file_lock(root, lock_path):
        pass


def test_exclusive_file_lock_rejects_path_outside_root(tmp_path: Path) -> None:
    # Given: the requested lock path escapes the declared cache root.
    root = tmp_path / "models"
    root.mkdir()
    outside_lock = tmp_path / "outside" / "materialize.lock"

    # When/Then: acquiring the lock fails before creating anything outside root.
    with pytest.raises(PathSafetyError), exclusive_file_lock(root, outside_lock):
        pass
    assert not outside_lock.parent.exists()
