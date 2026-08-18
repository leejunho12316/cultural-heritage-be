"""Deterministic English-to-Korean lookup for retrieval query expansion.

These are fixed dictionary translations of the existing allowlisted visual
vocabulary (see modules/rag/operations/candidate_term_constants.py) - never
invented per-candidate wording. They exist only to widen retrieval queries
against a Korean-language document corpus; they are never surfaced as
observed evidence, citations, or report claims.
"""

from typing import Final

CONCEPT_FAMILY_KOREAN_TERMS: Final[dict[str, tuple[str, ...]]] = {
    "crack": ("균열",),
    "deposit": ("오염물", "퇴적물"),
    "corrosion": ("부식",),
    "biological_growth": ("생물학적 성장", "생물 오염"),
    "surface_loss": ("표면 손실",),
    "flaking": ("박리",),
    "stain_discoloration": ("변색",),
    "hole_pit": ("구멍",),
    "deformation": ("변형",),
    "adhesive_residue": ("접착제 잔여물",),
}

DESCRIPTOR_KOREAN_TERMS: Final[dict[str, tuple[str, ...]]] = {
    "white": ("흰색",),
    "black": ("검은색",),
    "green": ("녹색",),
    "reddish": ("붉은색",),
    "yellow": ("노란색",),
    "gray": ("회색",),
    "line": ("선형",),
    "spot": ("반점",),
    "hole": ("구멍",),
    "pit": ("함몰",),
    "crust": ("경화층",),
    "powder": ("분말",),
    "flaking": ("박리",),
    "broad": ("넓은",),
    "powdery": ("분말상",),
    "crystalline": ("결정질",),
    "rough": ("거친",),
    "layered": ("층상",),
    "smooth": ("매끄러운",),
    "micro": ("미세",),
    "local": ("국소",),
}

MATERIAL_KOREAN_TERMS: Final[dict[str, tuple[str, ...]]] = {
    "ceramic": ("도자기",),
    "glass": ("유리",),
    "metal": ("금속",),
    "stone": ("석재",),
    "wood": ("목재",),
}

CONTEXT_KOREAN_TERMS: Final[dict[str, tuple[str, ...]]] = {
    "surface": ("표면",),
    "artifact": ("유물",),
    "area": ("영역",),
    "region": ("부위",),
    "localized": ("국소적인",),
}


# 영어 토큰 하나에 대해 4개 매핑 테이블을 순서대로 조회해 한국어 등가어를
# 찾는다. startup_runner._bilingual_query_text가 검색 쿼리를 넓힐 때 호출하며,
# 테이블에 없는 토큰은 조용히 빈 튜플을 반환한다(발명하지 않음).
def korean_terms_for_token(token: str) -> tuple[str, ...]:
    """Return known Korean equivalents for one lowercase English token."""
    for table in (
        CONCEPT_FAMILY_KOREAN_TERMS,
        DESCRIPTOR_KOREAN_TERMS,
        MATERIAL_KOREAN_TERMS,
        CONTEXT_KOREAN_TERMS,
    ):
        terms = table.get(token)
        if terms is not None:
            return terms
    return ()
