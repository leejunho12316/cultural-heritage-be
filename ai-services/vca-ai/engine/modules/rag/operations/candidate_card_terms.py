"""Map query prompts and explicit evidence terms into safe card fields."""

from typing import TYPE_CHECKING

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
    STRONG_PROVENANCE_MIN_MATCHED_TERMS,
    STRONG_PROVENANCE_MIN_SCORE,
    TABLE_GARBAGE_PHRASES,
    TABLE_GARBAGE_TERMS,
)
from modules.rag.qwen import qwen_bridge_query_terms

if TYPE_CHECKING:
    from collections.abc import Mapping

    from modules.shared import CandidateId, QwenBridgeResult


# 후보 고유 Qwen 서술어(selected_terms/extracted_descriptors)를 결정론적인
# 정렬·중복제거 튜플로 만든다. rag.qwen.qwen_bridge_query_terms()(기존 함수,
# ALLOWED_DESCRIPTOR_TERMS로 걸러지지 않고 anomaly-class 토큰만 제거된 넓은
# 어휘)를 그대로 재사용한다 - 여기서 다시 필터링하지 않는다. startup_runner의
# 질의 생성과 candidate_sidecars의 쿼리 조인이 이 함수 하나를 공유해서 같은
# 시그니처를 계산하도록 보장한다. Qwen 데이터가 없거나 실패 상태면 빈 튜플을
# 돌려줘, 호출부가 오늘과 동일한(시드 프롬프트 공유) 동작으로 폴백하게 한다.
def qwen_query_signature(
    candidate_id: "CandidateId",
    qwen_results: "Mapping[CandidateId, QwenBridgeResult]",
) -> tuple[str, ...]:
    """Return a stable per-candidate signature from Qwen query-driving terms."""
    bridge = qwen_results.get(candidate_id)
    if bridge is None:
        return ()
    terms = qwen_bridge_query_terms(bridge)
    if terms is None:
        return ()
    return tuple(sorted(set(terms.lexical_tokens)))


# 프롬프트 텍스트에서 FAMILY_KEYWORDS와 일치하는 첫 anomaly family를 찾는다.
# candidate_sidecars._build_candidate가 카드 생성 전에 호출한다.
def concept_family(prompt_text: str) -> VisualConceptFamily:
    """Return the allowlisted anomaly family encoded by prompt text."""
    text = prompt_text.casefold()
    for keywords, family in FAMILY_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return family
    return VisualConceptFamily.UNKNOWN_VISUAL_ANOMALY


# 검색 결과의 matched_terms와 시각 단서를 합쳐 카드용 서술어를 만든다.
# concept_family 토큰은 서술어와 중복되지 않도록 차단(blocked)한다.
# candidate_sidecars._card가 카드 조립 시 호출한다.
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


# 허용된 문맥 용어를 뽑되 "area"는 제외하고 "surface"를 항상 덧붙여, 결과가
# 절대 비지 않도록 보장한다. candidate_sidecars._card가 호출한다.
def context_terms(result: PromptRagResultRecord) -> tuple[str, ...]:
    """Return safe non-empty context terms for prompt rendering."""
    terms = tuple(
        term
        for term in safe_terms(result.matched_terms, ALLOWED_CONTEXT_TERMS, ())
        if term != "area"
    )
    return tuple(dict.fromkeys((*terms, "surface")))


# 검색 결과에서만 재질 용어를 추론한다(추측·기본값 없음).
# candidate_sidecars._card가 호출한다.
def material_terms(result: PromptRagResultRecord) -> tuple[str, ...]:
    """Infer allowlisted materials from retrieval evidence only."""
    return safe_terms(
        result.matched_terms,
        ALLOWED_MATERIAL_TERMS,
        (),
    )


# Qwen 결과가 없을 때 검색 결과만으로 결정적인 대체 시각 단서를 만든다.
# 신뢰도는 0.65로 고정된다. candidate_sidecars._first_prompt_ready_card가
# explicit_cue가 없는 경우에만 호출한다.
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


# 이미 허용된 서술어 값들로부터 시각 단서를 만든다. qwen_visual_cues가 Qwen의
# selected_terms/extracted_descriptors를 안전한 VisualCue로 변환할 때 호출한다.
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


# 이미 만들어진(주로 Qwen발) 시각 단서에서 실행 불가능한 잔여값을 정리한다.
# candidate_sidecars._first_prompt_ready_card가 explicit_cue가 있을 때 호출한다.
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


# 표/수치 데이터가 OCR로 잘못 추출된 스니펫을 걸러낸다(반복 구문, 숫자
# 비율 등 휴리스틱 사용). candidate_sidecars._first_prompt_ready_card가 결과를
# 카드 후보로 쓸지 판단할 때 먼저 호출한다.
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


# 검색 결과 하나가 concept card의 근거로 얼마나 강한지 판정한다.
# candidate_sidecars._card가 카드를 조립할 때 호출한다. RAG 시스템 자신의
# 검색 신뢰도(관련도 점수 + 실제 겹친 용어 수)만 보고, 손상 위험도 같은
# 도메인 판단은 하지 않는다 - 그건 검증된 기준이 없어 이 함수의 책임이 아니다.
def provenance_strength_for_result(result: PromptRagResultRecord) -> str:
    """Return "strong" only when both the retrieval score and the matched-term
    count clear their thresholds - either signal alone can be misleading."""
    if (
        result.score >= STRONG_PROVENANCE_MIN_SCORE
        and len(result.matched_terms) >= STRONG_PROVENANCE_MIN_MATCHED_TERMS
    ):
        return "strong"
    return "weak"


# 색상/형태/질감 중 하나라도 UNKNOWN이 아니면 프롬프트를 구별할 수 있는
# 단서로 본다. candidate_sidecars와 qwen_visual_cues 양쪽이 카드/단서 채택
# 여부를 최종 결정할 때 호출한다.
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


# VisualCue 필드들(색상/형태/질감)을 descriptor_terms가 쓸 수 있는 문자열
# 토큰으로 되돌린다. HOLE_PIT은 hole/pit 두 토큰으로 확장되는 점에 유의.
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


# "smooth"는 다른 서술어와 함께 나오면 정보가 없으므로 제거하고, 유일한
# 서술어일 때만 남긴다.
def _drop_smooth_when_not_alone(terms: tuple[str, ...]) -> tuple[str, ...]:
    if len(terms) <= 1:
        return terms
    return tuple(term for term in terms if term != "smooth")


def _is_numeric_like(token: str) -> bool:
    parts = tuple(part for part in token.split("-") if part)
    return bool(parts) and all(part.isdecimal() for part in parts)


# 이 모듈의 모든 term 추출 함수가 공유하는 핵심 필터: 허용 목록(allowlist)에
# 있고 차단 목록에 없는 토큰만 통과시킨다. 이 파일 전체의 "안전한 용어만
# 쓴다"는 불변식이 여기 한 곳에 모여 있다.
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


# concept family 이름을 안전 용어 필터의 blocked 인자로 쓰기 위해 토큰화한다.
# descriptor_terms/retrieval_visual_cue가 서술어에 anomaly class 이름 자체가
# 섞여 들어가는 것을 막기 위해 호출한다.
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
