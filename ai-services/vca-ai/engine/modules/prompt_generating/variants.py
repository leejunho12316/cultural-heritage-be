"""Visual-only, lane-specific executable prompt rendering."""

from hashlib import sha256
from typing import Final, Never

from modules.shared import ACTIVE_RAG_LANES, PromptMetadata, PromptRole, RagLane

from .models import (
    ConceptCard,
    Morphology,
    PromptSafetyError,
    PromptVariant,
    TextureProxy,
    VisualConceptFamily,
    VisualCue,
)

RAG_REFINEMENT_PACK_ID: Final = "rag-refinement-v1"
MAX_EXECUTABLE_PROMPT_LENGTH: Final = 120
# 비시각적 어휘뿐 아니라 명령 주입(prompt injection) 문구도 걸러낸다:
# source term은 RAG로 검색된 신뢰할 수 없는 외부 문장에서 오기 때문에,
# 그 문장이 명령 주입을 실어 나를 수 있어서다.
_BANNED_FRAGMENTS: Final = (
    "diagnos",
    "treatment",
    "severity",
    "urgent",
    "urgency",
    "repair",
    "restore",
    "restoration",
    "conserve",
    "conservation",
    "preservation",
    "진단",
    "처치",
    "치료",
    "보존처리",
    "심각도",
    "ignore previous",
    "instruction",
    "system prompt",
)
# 카드 표시용 서술어 허용어휘와 이 안전 검사 허용어휘는 하나의 목록이어야
# 한다 - concept card의 descriptor_terms가 여기서 안 걸러지면 카드에 실려
# 있다는 이유만으로 이미 안전이 보장된 셈이라, 프롬프트로 렌더링할 때
# 다시 걸러지면(더 좁은 목록) 실행 중 PromptSafetyError로 파이프라인
# 전체가 죽는다(실제로 한 번 이렇게 두 목록이 따로 놀아서 겪었음). 그래서
# modules.rag.operations.candidate_term_constants가 이 목록을 그대로
# import해서 쓰고, 별도로 유지하지 않는다 - 여기가 유일한 출처다.
ALLOWED_DESCRIPTOR_TERMS: Final = frozenset(
    {
        "white",
        "black",
        "green",
        "reddish",
        "yellow",
        "gray",
        "line",
        "spot",
        "hole",
        "pit",
        "crust",
        "powder",
        "flaking",
        "broad",
        "powdery",
        "crystalline",
        "rough",
        "layered",
        "smooth",
        "micro",
        "local",
        "irregular",
        "texture",
        "shape",
        "shapes",
        "patch",
        "patchy",
        "patches",
        "dark",
        "horizontal",
        "linear",
        "streak",
    }
)
ALLOWED_MATERIAL_TERMS: Final = frozenset(
    {"stone", "metal", "ceramic", "paint", "wood", "plaster", "textile", "glass"}
)
ALLOWED_CONTEXT_TERMS: Final = frozenset(
    {"surface", "artifact", "area", "region", "localized"}
)
_FAMILY_TEXT: Final = {
    VisualConceptFamily.CRACK: "crack",
    VisualConceptFamily.DEPOSIT: "deposit",
    VisualConceptFamily.CORROSION: "corrosion",
    VisualConceptFamily.BIOLOGICAL_GROWTH: "biological growth",
    VisualConceptFamily.SURFACE_LOSS: "surface loss",
    VisualConceptFamily.FLAKING: "flaking",
    VisualConceptFamily.STAIN_DISCOLORATION: "stain discoloration",
    VisualConceptFamily.HOLE_PIT: "hole or pit",
    VisualConceptFamily.DEFORMATION: "deformation",
    VisualConceptFamily.ADHESIVE_RESIDUE: "adhesive residue",
    VisualConceptFamily.UNKNOWN_VISUAL_ANOMALY: "visual anomaly",
}


def _unsafe(field: str, reason: str) -> Never:
    """Raise the sole typed error used by executable prompt safety checks."""
    raise PromptSafetyError(field, reason)


def _assert_safe_term(term: str, allowed_terms: frozenset[str]) -> None:
    """Reject non-visual, instruction-like, or unallowlisted source terms."""
    normalized = term.strip().casefold()
    if not normalized or normalized != term:
        _unsafe("source_term", "must be a normalized visual term")
    if any(fragment in normalized for fragment in _BANNED_FRAGMENTS):
        _unsafe("source_term", "contains non-visual or instruction language")
    if (
        ";" in normalized
        or "\n" in normalized
        or "<" in normalized
        or ">" in normalized
    ):
        _unsafe("source_term", "contains unsafe prompt syntax")
    if normalized not in allowed_terms:
        _unsafe("source_term", "is outside the visual allowlist")


def _assert_safe_terms(terms: tuple[str, ...], allowed_terms: frozenset[str]) -> None:
    """Validate a source-term tuple and reject duplicate rendering inputs."""
    if len(terms) != len(set(terms)):
        _unsafe("source_terms", "duplicate descriptor term")
    for term in terms:
        _assert_safe_term(term, allowed_terms)


def _cue_terms(cue: VisualCue) -> tuple[str, ...]:
    """Translate non-unknown cue values into allowlisted rendering terms."""
    terms: list[str] = []
    if cue.color_bucket.value != "unknown":
        terms.append(cue.color_bucket.value)
    match cue.morphology:
        case Morphology.HOLE_PIT:
            terms.extend(("hole", "pit"))
        case Morphology.UNKNOWN:
            pass
        case morphology:
            terms.append(morphology.value.replace("_patch", ""))
    match cue.texture_proxy:
        case TextureProxy.UNKNOWN:
            pass
        case texture:
            terms.append(texture.value)
    return tuple(terms)


def _unique_terms(terms: tuple[str, ...]) -> tuple[str, ...]:
    """Preserve the first occurrence of each allowed visual term."""
    return tuple(dict.fromkeys(terms))


def validate_executable_prompt(prompt: str) -> str:
    """Reject prompts exceeding syntax, length, or non-visual safety boundaries."""
    normalized = " ".join(prompt.split()).casefold()
    if len(normalized) > MAX_EXECUTABLE_PROMPT_LENGTH:
        _unsafe("generated_prompt", "exceeds maximum length")
    if not normalized:
        _unsafe("generated_prompt", "must not be empty")
    if (
        ";" in normalized
        or "\n" in normalized
        or "<" in normalized
        or ">" in normalized
    ):
        _unsafe("generated_prompt", "contains unsafe prompt syntax")
    if any(fragment in normalized for fragment in _BANNED_FRAGMENTS):
        _unsafe("generated_prompt", "contains non-visual or instruction language")
    return normalized


def _render_prompt(card: ConceptCard, cue: VisualCue, lane: RagLane) -> str:
    """Render a single lane phrase from allowlisted card and cue terms."""
    family = card.concept_family
    if family is None:
        _unsafe("concept_family", "concept family is required")
    _assert_safe_terms(card.descriptor_terms, ALLOWED_DESCRIPTOR_TERMS)
    cue_terms = _cue_terms(cue)
    _assert_safe_terms(cue_terms, ALLOWED_DESCRIPTOR_TERMS)
    _assert_safe_terms(card.material_terms, ALLOWED_MATERIAL_TERMS)
    _assert_safe_terms(card.context_terms, ALLOWED_CONTEXT_TERMS)
    phrase = " ".join(
        _unique_terms(
            (*card.descriptor_terms, *cue_terms, _FAMILY_TEXT[family])
        )
    )
    location = " ".join(card.material_terms + card.context_terms)
    match lane:
        case RagLane.OWLV2:
            return validate_executable_prompt(phrase)
        case RagLane.GROUNDINGDINO:
            if not location:
                _unsafe("location_terms", "location terms are required")
            return validate_executable_prompt(f"localized {phrase} on {location}")
        case RagLane.FLORENCE2:
            _unsafe("lane", "florence2 is non-active")


def _prompt_id(card: ConceptCard, lane: RagLane, prompt: str) -> str:
    """Create a stable metadata identity that preserves distinct concept cards."""
    canonical = f"{card.concept_card_id}\x1f{lane.value}\x1f{prompt}"
    digest = sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{RAG_REFINEMENT_PACK_ID}-{lane.value}-{digest}"


def render_lane_specific_variants(
    card: ConceptCard,
    cue: VisualCue,
) -> tuple[PromptVariant, ...]:
    """Render one safe phrase per active RAG lane from visual data only."""
    family = card.concept_family
    if family is None:
        _unsafe("concept_family", "concept family is required")
    variants: list[PromptVariant] = []
    source_terms = _unique_terms(
        (
            family.value,
            *card.descriptor_terms,
            *_cue_terms(cue),
            *card.material_terms,
            *card.context_terms,
        )
    )
    for lane in ACTIVE_RAG_LANES:
        prompt = _render_prompt(card, cue, lane)
        variants.append(
            PromptVariant(
                metadata=PromptMetadata(
                    prompt_pack_id=RAG_REFINEMENT_PACK_ID,
                    prompt_role=PromptRole.RAG_REFINEMENT,
                    model_lane=lane,
                    generated_prompt_id=_prompt_id(card, lane, prompt),
                    source_terms=source_terms,
                    source_citation_ids=card.source_citation_ids,
                ),
                generated_prompt=prompt,
                model_prompt_variant=lane.value,
                concept_card_id=card.concept_card_id,
                rag_parent_candidate_id=card.rag_parent_candidate_id,
                source_concept_family=family,
            )
        )
    return tuple(variants)


def validate_unique_prompt_texts(variants: tuple[PromptVariant, ...]) -> None:
    """Block duplicate executable prompt text for the same input target.

    Uniqueness is scoped per rag_parent_candidate_id (one rough candidate -
    one specific image/object/tile). Two different candidates - e.g. the
    same kind of damage seen on two different images - can legitimately
    render identical prompt text; that must not fail the whole project.
    """
    seen_by_target: dict[str, set[str]] = {}
    for variant in variants:
        seen = seen_by_target.setdefault(variant.rag_parent_candidate_id, set())
        if variant.generated_prompt in seen:
            _unsafe("generated_prompt", "duplicate rendered prompt text")
        seen.add(variant.generated_prompt)
