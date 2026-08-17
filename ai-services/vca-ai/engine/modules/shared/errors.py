"""Typed exceptions for invalid shared-pipeline contracts."""

from dataclasses import dataclass
from typing import override


@dataclass(frozen=True, slots=True)
class ContractValidationError(ValueError):
    """Raised when a typed pipeline contract cannot be constructed."""

    field: str
    reason: str

    @override
    def __str__(self) -> str:
        """Render the invalid contract field and its reason."""
        return f"invalid contract field {self.field!r}: {self.reason}"


@dataclass(frozen=True, slots=True)
class PathSafetyError(ValueError):
    """Raised when a path violates a pre-write safety boundary."""

    path: str
    reason: str

    @override
    def __str__(self) -> str:
        """Render the unsafe path and its rejected condition."""
        return f"unsafe path {self.path!r}: {self.reason}"
