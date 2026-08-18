"""Typed exceptions for invalid shared-pipeline contracts."""

from dataclasses import dataclass
from typing import override

# frozen=True는 일부러 안 쓴다 - CPython이 예외를 재던질 때(예: @contextmanager
# 제너레이터의 gen.throw() 경로, pytest.raises의 __exit__) 같은 인스턴스에
# exc.__traceback__ = traceback으로 트레이스백을 다시 붙이는데, frozen dataclass의
# __setattr__은 필드 여부와 무관하게 모든 대입을 막아서 그 순간
# FrozenInstanceError/TypeError로 원래 예외를 가려버린다. slots=True만으로는 이
# 문제가 없다 - __traceback__은 BaseException 자체의 C 레벨 슬롯이라 별도
# __setattr__ 오버라이드가 없으면 정상 동작한다.


@dataclass(slots=True)
class ContractValidationError(ValueError):
    """Raised when a typed pipeline contract cannot be constructed."""

    field: str
    reason: str

    @override
    def __str__(self) -> str:
        """Render the invalid contract field and its reason."""
        return f"invalid contract field {self.field!r}: {self.reason}"


@dataclass(slots=True)
class PathSafetyError(ValueError):
    """Raised when a path violates a pre-write safety boundary."""

    path: str
    reason: str

    @override
    def __str__(self) -> str:
        """Render the unsafe path and its rejected condition."""
        return f"unsafe path {self.path!r}: {self.reason}"
