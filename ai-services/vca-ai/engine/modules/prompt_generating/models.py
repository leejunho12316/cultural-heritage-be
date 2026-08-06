"""Immutable prompt-generation records and closed visual vocabularies."""

from dataclasses import dataclass
from enum import StrEnum
from typing import override

from modules.shared import PromptMetadata, PromptRole


class ColorBucket(StrEnum):
    """Allowed dominant-color buckets for candidate cues."""

    WHITE = "white"
    BLACK = "black"
    GREEN = "green"
    REDDISH = "reddish"
    YELLOW = "yellow"
    GRAY = "gray"
    UNKNOWN = "unknown"


class Morphology(StrEnum):
    """Allowed morphology buckets for candidate cues."""

    LINE = "line"
    SPOT = "spot"
    HOLE_PIT = "hole_pit"
    CRUST = "crust"
    POWDER = "powder"
    FLAKING_PATCH = "flaking_patch"
    BROAD_PATCH = "broad_patch"
    UNKNOWN = "unknown"


class TextureProxy(StrEnum):
    """Allowed texture terms; crystalline is reserved for RAG concept cards."""

    POWDERY = "powdery"
    CRYSTALLINE = "crystalline"
    ROUGH = "rough"
    LAYERED = "layered"
    SMOOTH = "smooth"
    UNKNOWN = "unknown"


class SizeClass(StrEnum):
    """Allowed candidate-area size classes."""

    MICRO = "micro"
    LOCAL = "local"
    BROAD = "broad"
    UNKNOWN = "unknown"


class BoundaryRelation(StrEnum):
    """Allowed object-boundary relations for candidate cues."""

    INTERIOR = "interior"
    BOUNDARY = "boundary"
    CROSSING = "crossing"
    UNKNOWN = "unknown"


class VisualConceptFamily(StrEnum):
    """Allowlisted visual concept families for executable prompts."""

    CRACK = "crack"
    DEPOSIT = "deposit"
    CORROSION = "corrosion"
    BIOLOGICAL_GROWTH = "biological_growth"
    SURFACE_LOSS = "surface_loss"
    FLAKING = "flaking"
    STAIN_DISCOLORATION = "stain_discoloration"
    HOLE_PIT = "hole_pit"
    DEFORMATION = "deformation"
    ADHESIVE_RESIDUE = "adhesive_residue"
    UNKNOWN_VISUAL_ANOMALY = "unknown_visual_anomaly"


@dataclass(frozen=True, slots=True)
class PromptSafetyError(ValueError):
    """Raised when data cannot form a visual-only executable prompt."""

    field: str
    reason: str

    @override
    def __str__(self) -> str:
        return f"unsafe prompt field {self.field!r}: {self.reason}"


@dataclass(frozen=True, slots=True)
class PromptRecord:
    """One immutable executable seed prompt and its provenance."""

    metadata: PromptMetadata
    prompt_text: str
    rough_target_anchor_only: bool
    final_conservation_vocabulary: bool
    anomaly_class_proof: bool


@dataclass(frozen=True, slots=True)
class PromptPack:
    """Role-labeled collection of prompt records."""

    prompt_pack_id: str
    prompt_role: PromptRole
    records: tuple[PromptRecord, ...]


@dataclass(frozen=True, slots=True)
class RgbColor:
    """Synthetic scalar color fixture independent of image libraries."""

    red: int
    green: int
    blue: int


@dataclass(frozen=True, slots=True)
class CueMeasurements:
    """Scalar candidate measurements consumed by deterministic cue extraction."""

    dominant_rgb: RgbColor | None
    mask_area_ratio: float
    aspect_ratio: float
    void_ratio: float
    fragmentation: float
    texture_variance: float
    boundary_overlap_ratio: float | None


@dataclass(frozen=True, slots=True)
class VisualCue:
    """Model-independent visual descriptors with deterministic evidence reasons."""

    color_bucket: ColorBucket
    morphology: Morphology
    texture_proxy: TextureProxy
    size_class: SizeClass
    boundary_relation: BoundaryRelation
    confidence: float
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConceptCard:
    """RAG concept metadata whose raw sentence remains citation-only data."""

    concept_card_id: str
    rag_parent_candidate_id: str
    concept_family: VisualConceptFamily | None
    descriptor_terms: tuple[str, ...]
    material_terms: tuple[str, ...]
    context_terms: tuple[str, ...]
    source_citation_ids: tuple[str, ...]
    raw_retrieved_sentence: str


@dataclass(frozen=True, slots=True)
class PromptVariant:
    """One lane-specific executable prompt retaining concept provenance."""

    metadata: PromptMetadata
    generated_prompt: str
    model_prompt_variant: str
    concept_card_id: str
    rag_parent_candidate_id: str
    source_concept_family: VisualConceptFamily
