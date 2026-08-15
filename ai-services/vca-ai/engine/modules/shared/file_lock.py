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
    """블록 본문 동안 lock_path에 스코프된, 블로킹되는 OS 레벨 락을 유지한다.

    같은 모델 캐시 루트를 공유하는 동시 프로세스들(예: 실제 파이프라인 실행
    두 개)이, 파생 캐시 아티팩트를 다시 만들고 저장하는 것 같은 하나의
    크리티컬 섹션을 순서대로 실행하도록 직렬화한다. 재진입 불가능하다:
    같은 프로세스에서 같은 락을 두 번 획득하려 하면 POSIX에서는 데드락이,
    Windows에서는 예외가 발생한다.
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
