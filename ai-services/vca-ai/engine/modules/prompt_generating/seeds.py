"""Locked static seed prompt packs and refinement-role selection."""

from typing import Final

from modules.prompt_generating.models import PromptPack, PromptRecord, PromptSafetyError
from modules.shared import PromptMetadata, PromptRole, RagLane

STATIC_SEED_MINIMAL_PACK_ID: Final = "static-seed-minimal-v1"
STATIC_OBJECT_DETECTION_PACK_ID: Final = "static-object-detection-v1"
STATIC_FALLBACK_ABLATION_PACK_ID: Final = "static-fallback-ablation-v1"
_PROMPT_ROLE_FIELD: Final = "prompt_role"


def _seed_record(lane: RagLane, prompt_text: str, order: int) -> PromptRecord:
    """Build one locked rough-target seed record with a stable identity."""
    prompt_id = f"{STATIC_SEED_MINIMAL_PACK_ID}-{lane.value}-{order:02d}"
    return PromptRecord(
        metadata=PromptMetadata(
            prompt_pack_id=STATIC_SEED_MINIMAL_PACK_ID,
            prompt_role=PromptRole.STATIC_SEED,
            model_lane=lane,
            generated_prompt_id=prompt_id,
            source_terms=(prompt_text,),
            source_citation_ids=(),
        ),
        prompt_text=prompt_text,
        rough_target_anchor_only=True,
        final_conservation_vocabulary=False,
        anomaly_class_proof=False,
    )


# static_object_detection_pack의 각 레코드를 만든다. _seed_record와 달리
# rough_target_anchor_only=False로, 손상 부위가 아닌 유물 객체 자체를
# 탐지하기 위한 고정 프롬프트임을 나타낸다.
def _object_record(lane: RagLane, prompt_text: str, order: int) -> PromptRecord:
    prompt_id = f"{STATIC_OBJECT_DETECTION_PACK_ID}-{lane.value}-{order:02d}"
    return PromptRecord(
        metadata=PromptMetadata(
            prompt_pack_id=STATIC_OBJECT_DETECTION_PACK_ID,
            prompt_role=PromptRole.STATIC_SEED,
            model_lane=lane,
            generated_prompt_id=prompt_id,
            source_terms=(prompt_text,),
            source_citation_ids=(),
        ),
        prompt_text=prompt_text,
        rough_target_anchor_only=False,
        final_conservation_vocabulary=False,
        anomaly_class_proof=False,
    )


# 의도적으로 작게 고정된 목록이다 (5개 프롬프트 x 3개 lane). 이 고정된
# 풀 때문에 프로젝트 전역에서 RAG 검색이 극소수의 쿼리로 수렴한다;
# 풀을 늘리면 이 팩뿐 아니라 RAG 쿼리 다양성 전반에 파급 효과가 있다.
static_seed_minimal_pack: Final = PromptPack(
    prompt_pack_id=STATIC_SEED_MINIMAL_PACK_ID,
    prompt_role=PromptRole.STATIC_SEED,
    records=(
        _seed_record(RagLane.OWLV2, "surface crack", 1),
        _seed_record(RagLane.OWLV2, "spalled surface", 2),
        _seed_record(RagLane.OWLV2, "dark stain", 3),
        _seed_record(RagLane.OWLV2, "crusty deposit", 4),
        _seed_record(RagLane.OWLV2, "corrosion pit", 5),
        _seed_record(RagLane.GROUNDINGDINO, "crack or fissure on the surface", 1),
        _seed_record(RagLane.GROUNDINGDINO, "flaking or spalling area", 2),
        _seed_record(RagLane.GROUNDINGDINO, "dark discoloration or stain", 3),
        _seed_record(RagLane.GROUNDINGDINO, "hard mineral deposit or accretion", 4),
        _seed_record(RagLane.GROUNDINGDINO, "corrosion spot with pitting", 5),
    ),
)

static_object_detection_pack: Final = PromptPack(
    prompt_pack_id=STATIC_OBJECT_DETECTION_PACK_ID,
    prompt_role=PromptRole.STATIC_SEED,
    records=(
        _object_record(RagLane.OWLV2, "artifact object", 1),
        _object_record(RagLane.OWLV2, "individual artifact", 2),
        _object_record(RagLane.OWLV2, "whole artifact", 3),
        _object_record(RagLane.OWLV2, "separate object on white background", 4),
        _object_record(RagLane.OWLV2, "metal artifact on white background", 5),
        _object_record(RagLane.GROUNDINGDINO, "artifact object", 1),
        _object_record(RagLane.GROUNDINGDINO, "individual artifact", 2),
        _object_record(RagLane.GROUNDINGDINO, "whole artifact", 3),
        _object_record(RagLane.GROUNDINGDINO, "separate object on white background", 4),
        _object_record(RagLane.GROUNDINGDINO, "metal artifact on white background", 5),
    ),
)

static_fallback_ablation_pack: Final = PromptPack(
    prompt_pack_id=STATIC_FALLBACK_ABLATION_PACK_ID,
    prompt_role=PromptRole.STATIC_FALLBACK_ABLATION,
    records=(),
)


def primary_refinement_role(
    prompt_role: PromptRole,
    *,
    ablation_mode: bool = False,
) -> PromptRole:
    """Allow the fallback role only for an explicitly declared ablation."""
    match prompt_role:
        case PromptRole.RAG_REFINEMENT:
            return prompt_role
        case PromptRole.STATIC_FALLBACK_ABLATION if ablation_mode:
            return prompt_role
        case PromptRole.STATIC_FALLBACK_ABLATION:
            reason = "fallback requires ablation mode"
            raise PromptSafetyError(_PROMPT_ROLE_FIELD, reason)
        case PromptRole.STATIC_SEED:
            reason = "seed is a rough-target anchor"
            raise PromptSafetyError(_PROMPT_ROLE_FIELD, reason)
