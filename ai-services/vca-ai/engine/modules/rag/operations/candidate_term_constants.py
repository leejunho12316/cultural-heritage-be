"""Allowlisted term tables for candidate concept-card construction."""

from typing import Final

from modules.prompt_generating import (
    ALLOWED_CONTEXT_TERMS,
    ALLOWED_DESCRIPTOR_TERMS,
    ALLOWED_MATERIAL_TERMS,
    VisualConceptFamily,
)

__all__ = (
    "ALLOWED_CONTEXT_TERMS",
    "ALLOWED_DESCRIPTOR_TERMS",
    "ALLOWED_MATERIAL_TERMS",
    "FAMILY_KEYWORDS",
    "MIN_NUMERIC_FRAGMENT_COUNT",
    "MIN_REPEATED_FRAGMENT_COUNT",
    "MIN_TABLE_GARBAGE_NUMERIC_COUNT",
    "TABLE_GARBAGE_PHRASES",
    "TABLE_GARBAGE_TERMS",
)

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
# ALLOWED_DESCRIPTOR_TERMS/ALLOWED_MATERIAL_TERMS/ALLOWED_CONTEXT_TERMS는
# modules.prompt_generating.variants가 유일한 출처다(위에서 그대로 import).
# 여기서 따로 복제하지 않는다 - 카드 구성 시점의 허용어휘와 프롬프트 렌더링
# 시점의 안전 검사 허용어휘가 따로 놀면, 카드에는 실렸지만 프롬프트로
# 렌더링할 때 걸러지는 서술어가 생겨 파이프라인이 PromptSafetyError로
# 죽는다(실측 근거: qwen_bridge_result.jsonl 523건 기준 원래 21단어 커버리지
# 43.4% -> prompt_generating 쪽 허용어휘 확장 후 100%).
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

# concept card의 provenance_strength 판정 기준. 두 신호(검색 관련도 점수 +
# 실제로 겹친 용어 개수)가 둘 다 만족돼야 "strong" - 점수 하나만으로는
# 우연히 높게 나온 매칭을 걸러내지 못하기 때문이다.
STRONG_PROVENANCE_MIN_SCORE: Final = 0.75
STRONG_PROVENANCE_MIN_MATCHED_TERMS: Final = 2
