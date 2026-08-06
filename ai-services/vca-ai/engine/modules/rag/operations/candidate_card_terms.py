"""Map query prompts and explicit evidence terms into safe card fields."""

from modules.prompt_generating import (
    BoundaryRelation,
    ColorBucket,
    Morphology,
    SizeClass,
    TextureProxy,
    VisualConceptFamily,
    VisualCue,
)
from modules.rag.operations.candidate_sidecar_artifacts import PromptRagResultRecord
from modules.rag.operations.candidate_term_constants import (
    ALLOWED_CONTEXT_TERMS,
    ALLOWED_DESCRIPTOR_TERMS,
    ALLOWED_MATERIAL_TERMS,
    FAMILY_KEYWORDS,
    MIN_NUMERIC_FRAGMENT_COUNT,
    MIN_REPEATED_FRAGMENT_COUNT,
    MIN_TABLE_GARBAGE_NUMERIC_COUNT,
    TABLE_GARBAGE_PHRASES,
    TABLE_GARBAGE_TERMS,
)


def concept_family(prompt_text: str) -> VisualConceptFamily:
    """Return the allowlisted anomaly family encoded by prompt text."""
    text = prompt_text.casefold()
    for keywords, family in FAMILY_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return family
    return VisualConceptFamily.UNKNOWN_VISUAL_ANOMALY


def descriptor_terms(
    result: PromptRagResultRecord,
    cue: VisualCue,
    family: VisualConceptFamily,
) -> tuple[str, ...]:
    """Build safe descriptor terms without duplicating anomaly class tokens."""
    blocked = family_tokens(family)
    candidates = (*result.matched_terms, *_visual_cue_descriptor_terms(cue))
    return _drop_smooth_when_not_alone(
        safe_terms(candidates, ALLOWED_DESCRIPTOR_TERMS, blocked)
    )


def context_terms(result: PromptRagResultRecord) -> tuple[str, ...]:
    """Return safe non-empty context terms for prompt rendering."""
    terms = tuple(
        term
        for term in safe_terms(result.matched_terms, ALLOWED_CONTEXT_TERMS, ())
        if term != "area"
    )
    return tuple(dict.fromkeys((*terms, "surface")))


def material_terms(result: PromptRagResultRecord) -> tuple[str, ...]:
    """Infer allowlisted materials from retrieval evidence only."""
    return safe_terms(
        result.matched_terms,
        ALLOWED_MATERIAL_TERMS,
        (),
    )


def retrieval_visual_cue(
    result: PromptRagResultRecord, family: VisualConceptFamily
) -> VisualCue | None:
    """Build a deterministic pre-Qwen cue from retrieval-safe terms."""
    terms = safe_terms(
        result.matched_terms, ALLOWED_DESCRIPTOR_TERMS, family_tokens(family)
    )
    if not terms:
        return None
    return VisualCue(
        color_bucket=_cue_color(terms),
        morphology=_cue_morphology(terms),
        texture_proxy=_cue_texture(terms),
        size_class=_cue_size(terms),
        boundary_relation=BoundaryRelation.UNKNOWN,
        confidence=0.65,
        reasons=terms,
    )


def visual_cue_from_descriptor_terms(
    values: tuple[str, ...], confidence: float
) -> VisualCue | None:
    """Build a visual cue from already-approved descriptor-bearing values."""
    terms = _drop_smooth_when_not_alone(
        safe_terms(values, ALLOWED_DESCRIPTOR_TERMS, ())
    )
    if not terms:
        return None
    return VisualCue(
        color_bucket=_cue_color(terms),
        morphology=_cue_morphology(terms),
        texture_proxy=_cue_texture(terms),
        size_class=_cue_size(terms),
        boundary_relation=BoundaryRelation.UNKNOWN,
        confidence=confidence,
        reasons=terms,
    )


def normalize_visual_cue(cue: VisualCue) -> VisualCue:
    """Remove non-actionable compatibility leftovers from explicit cues."""
    reasons = _drop_smooth_when_not_alone(cue.reasons)
    texture_proxy = cue.texture_proxy
    if cue.texture_proxy.value not in reasons:
        texture_proxy = TextureProxy.UNKNOWN
    return VisualCue(
        color_bucket=cue.color_bucket,
        morphology=cue.morphology,
        texture_proxy=texture_proxy,
        size_class=cue.size_class,
        boundary_relation=cue.boundary_relation,
        confidence=cue.confidence,
        reasons=reasons,
    )


def is_usable_retrieval_result(result: PromptRagResultRecord) -> bool:
    """Reject table-like OCR fragments that cannot support visual prompts."""
    tokens = tuple(
        raw.strip(".,:;()[]{}").casefold()
        for raw in result.snippet_text.replace("_", " ").split()
    )
    if not tokens:
        return False
    normalized = " ".join(tokens)
    phrase_hits = sum(phrase in normalized for phrase in TABLE_GARBAGE_PHRASES)
    if phrase_hits >= MIN_REPEATED_FRAGMENT_COUNT:
        return False
    numeric_like = sum(_is_numeric_like(token) for token in tokens)
    repeated = len(tokens) - len(set(tokens))
    if (
        any(term in tokens for term in TABLE_GARBAGE_TERMS)
        and numeric_like >= MIN_TABLE_GARBAGE_NUMERIC_COUNT
    ):
        return False
    return not (
        numeric_like >= MIN_NUMERIC_FRAGMENT_COUNT
        and repeated >= MIN_REPEATED_FRAGMENT_COUNT
    )


def is_actionable_visual_cue(cue: VisualCue) -> bool:
    """Return whether a cue contains prompt-discriminative visual evidence."""
    if cue.color_bucket is not ColorBucket.UNKNOWN:
        return True
    if cue.morphology is not Morphology.UNKNOWN:
        return True
    return cue.texture_proxy in (
        TextureProxy.POWDERY,
        TextureProxy.CRYSTALLINE,
        TextureProxy.ROUGH,
        TextureProxy.LAYERED,
    )


def _visual_cue_descriptor_terms(cue: VisualCue) -> tuple[str, ...]:
    terms: list[str] = []
    if cue.color_bucket is not ColorBucket.UNKNOWN:
        terms.append(cue.color_bucket.value)
    match cue.morphology:
        case Morphology.HOLE_PIT:
            terms.extend(("hole", "pit"))
        case Morphology.UNKNOWN:
            pass
        case morphology:
            terms.append(morphology.value.replace("_patch", ""))
    if cue.texture_proxy in (
        TextureProxy.POWDERY,
        TextureProxy.CRYSTALLINE,
        TextureProxy.ROUGH,
        TextureProxy.LAYERED,
    ):
        terms.append(cue.texture_proxy.value)
    return _drop_smooth_when_not_alone(tuple(dict.fromkeys(terms)))


def _drop_smooth_when_not_alone(terms: tuple[str, ...]) -> tuple[str, ...]:
    if len(terms) <= 1:
        return terms
    return tuple(term for term in terms if term != "smooth")


def _is_numeric_like(token: str) -> bool:
    parts = tuple(part for part in token.split("-") if part)
    return bool(parts) and all(part.isdecimal() for part in parts)


def safe_terms(
    values: tuple[str, ...],
    allowed: frozenset[str],
    blocked: tuple[str, ...],
) -> tuple[str, ...]:
    """Normalize and filter values through an explicit term allowlist."""
    terms: list[str] = []
    for value in values:
        for raw_term in value.casefold().replace("_", " ").split():
            term = raw_term.strip(".,:;()[]{}")
            if term in allowed and term not in blocked:
                terms.append(term)
    return tuple(dict.fromkeys(terms))


def family_tokens(family: VisualConceptFamily) -> tuple[str, ...]:
    """Split a concept family into tokens excluded from descriptors."""
    return tuple(family.value.split("_"))


def _cue_color(terms: tuple[str, ...]) -> ColorBucket:
    for term, color in (
        ("white", ColorBucket.WHITE),
        ("black", ColorBucket.BLACK),
        ("green", ColorBucket.GREEN),
        ("reddish", ColorBucket.REDDISH),
        ("yellow", ColorBucket.YELLOW),
        ("gray", ColorBucket.GRAY),
    ):
        if term in terms:
            return color
    return ColorBucket.UNKNOWN


def _cue_morphology(terms: tuple[str, ...]) -> Morphology:
    if "hole" in terms or "pit" in terms:
        return Morphology.HOLE_PIT
    for term, morphology in (
        ("line", Morphology.LINE),
        ("spot", Morphology.SPOT),
        ("crust", Morphology.CRUST),
        ("powder", Morphology.POWDER),
        ("flaking", Morphology.FLAKING_PATCH),
        ("broad", Morphology.BROAD_PATCH),
    ):
        if term in terms:
            return morphology
    return Morphology.UNKNOWN


def _cue_texture(terms: tuple[str, ...]) -> TextureProxy:
    for term, texture in (
        ("powdery", TextureProxy.POWDERY),
        ("crystalline", TextureProxy.CRYSTALLINE),
        ("rough", TextureProxy.ROUGH),
        ("layered", TextureProxy.LAYERED),
        ("smooth", TextureProxy.SMOOTH),
    ):
        if term in terms:
            return texture
    return TextureProxy.UNKNOWN


def _cue_size(terms: tuple[str, ...]) -> SizeClass:
    for term, size in (
        ("micro", SizeClass.MICRO),
        ("local", SizeClass.LOCAL),
        ("broad", SizeClass.BROAD),
    ):
        if term in terms:
            return size
    return SizeClass.UNKNOWN
