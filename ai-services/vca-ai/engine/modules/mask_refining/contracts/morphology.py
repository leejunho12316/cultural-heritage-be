"""Closed Qwen morphology vocabulary and descriptor projection."""

from collections.abc import Mapping
from typing import Final

QWEN_MORPHOLOGY_VALUES: Final = frozenset(
    {
        "line",
        "spot",
        "hole_pit",
        "crust",
        "powder",
        "flaking_patch",
        "broad_patch",
        "unknown",
    }
)
_UNKNOWN_MORPHOLOGY_VALUES: Final = frozenset(
    {"unknown", "unknown_morphology", "n_a", "na", "none", "null", "not_sure"}
)
_GENERIC_MORPHOLOGY_VALUES: Final = frozenset(
    {"smooth", "shiny", "polished", "round", "metallic", "wood", "stone", "surface"}
)
_MORPHOLOGY_DESCRIPTOR_TOKENS: Final[Mapping[str, tuple[str, ...]]] = {
    "line": ("line",),
    "spot": ("spot",),
    "hole_pit": ("hole", "pit"),
    "crust": ("crust",),
    "powder": ("powder",),
    "flaking_patch": ("flaking",),
    "broad_patch": ("broad",),
    "unknown": (),
}


def normalize_qwen_morphology(raw: str) -> str | None:
    """Return the parsed morphology value, or None when unsupported."""
    value = "_".join(raw.casefold().replace("-", " ").replace("_", " ").split())
    if (
        not value
        or value in _UNKNOWN_MORPHOLOGY_VALUES
        or value in _GENERIC_MORPHOLOGY_VALUES
    ):
        return "unknown"
    return value if value in QWEN_MORPHOLOGY_VALUES else None


def morphology_descriptor_terms(morphology: str) -> tuple[str, ...]:
    """Return safe descriptor terms for a parsed morphology value."""
    return _MORPHOLOGY_DESCRIPTOR_TOKENS[morphology]
