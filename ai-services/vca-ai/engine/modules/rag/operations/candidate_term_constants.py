"""Allowlisted term tables for candidate concept-card construction."""

from typing import Final

from modules.prompt_generating import VisualConceptFamily

FAMILY_KEYWORDS: Final = (
    (("crack", "fissure"), VisualConceptFamily.CRACK),
    (("deposit", "accretion"), VisualConceptFamily.DEPOSIT),
    (("corrosion", "pitting"), VisualConceptFamily.CORROSION),
    (("biological", "growth"), VisualConceptFamily.BIOLOGICAL_GROWTH),
    (("flaking", "spalling"), VisualConceptFamily.FLAKING),
    (("loss", "spalled"), VisualConceptFamily.SURFACE_LOSS),
    (("stain", "discoloration"), VisualConceptFamily.STAIN_DISCOLORATION),
    (("hole", "pit"), VisualConceptFamily.HOLE_PIT),
    (("deformation",), VisualConceptFamily.DEFORMATION),
    (("adhesive", "residue"), VisualConceptFamily.ADHESIVE_RESIDUE),
)
ALLOWED_DESCRIPTOR_TERMS: Final = frozenset(
    (
        "white", "black", "green", "reddish", "yellow", "gray", "line", "spot",
        "hole", "pit", "crust", "powder", "flaking", "broad", "powdery",
        "crystalline", "rough", "layered", "smooth", "micro", "local",
    )
)
ALLOWED_CONTEXT_TERMS: Final = frozenset(
    ("surface", "artifact", "area", "region", "localized")
)
ALLOWED_MATERIAL_TERMS: Final = frozenset(
    ("ceramic", "glass", "metal", "stone", "wood")
)
TABLE_GARBAGE_TERMS: Final = frozenset({"unchanging"})
TABLE_GARBAGE_PHRASES: Final = frozenset(
    (
        "after after",
        "before before",
        "changing area",
        "image differencing",
        "monochrome processing",
        "vnir image",
    )
)
MIN_TABLE_GARBAGE_NUMERIC_COUNT: Final = 2
MIN_NUMERIC_FRAGMENT_COUNT: Final = 3
MIN_REPEATED_FRAGMENT_COUNT: Final = 2
