"""Safe target selection for corpus cache and sidecar writes."""

from dataclasses import dataclass
from pathlib import Path, PurePath

from modules.shared import (
    ContractValidationError,
    PathSafetyError,
    ensure_source_document_is_not_write_target,
)


@dataclass(frozen=True, slots=True)
class CacheFallback:
    """A relative cache location outside the read-only source-document root."""

    allowed_root: Path
    root: Path
    relative_path: PurePath

    def __post_init__(self) -> None:
        """Reject cache paths that could escape the declared fallback root."""
        if (
            self.relative_path == PurePath()
            or self.relative_path.is_absolute()
            or ".." in self.relative_path.parts
        ):
            field = "relative_path"
            reason = "cache path must be contained and relative"
            raise ContractValidationError(field, reason)


# 캐시/사이드카 파일을 어디에 쓸지 결정하는 공용 안전 게이트. 읽기 전용
# source_document_root 안에는 절대 쓰지 못하게 하고, 지정된 fallback.root
# 밖으로도 벗어나지 못하게 이중으로 검사한다.
# document_corpus.resolve_extraction_cache_target과 startup_corpus_cache의
# 캐시/지문 경로 계산이 호출한다.
def safe_cache_target(source_document_root: Path, fallback: CacheFallback) -> Path:
    """Return a resolved fallback target only when shared path safety permits it."""
    resolved_allowed_root = fallback.allowed_root.expanduser().resolve()
    resolved_root = fallback.root.expanduser().resolve()
    if not _is_contained(resolved_root, resolved_allowed_root):
        raise PathSafetyError(str(fallback.root), "cache root escapes allowed root")
    target = ensure_source_document_is_not_write_target(
        source_document_root, fallback.root / fallback.relative_path
    )
    if not _is_contained(target, resolved_root):
        raise PathSafetyError(str(target), "cache target escapes fallback root")
    return target


def _is_contained(candidate: Path, root: Path) -> bool:
    return candidate == root or candidate.is_relative_to(root)
