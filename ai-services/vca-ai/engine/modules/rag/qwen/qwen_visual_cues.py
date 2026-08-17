"""Adapters from safe Qwen bridge terms to prompt-generation visual cues."""

from collections.abc import Mapping
from types import MappingProxyType

from modules.prompt_generating import VisualCue
from modules.rag.operations.candidate_card_terms import (
    is_actionable_visual_cue,
    visual_cue_from_descriptor_terms,
)
from modules.shared import CandidateId, QwenBridgeResult, QwenBridgeStatus

_DEFAULT_QWEN_CUE_CONFIDENCE = 0.55
_WEAK_QWEN_CUE_CONFIDENCE = 0.55


# Qwen 브리지 결과 하나를 시각 단서로 변환한다. 실패 상태거나 실행 불가능한
# 단서면 None을 돌려줘 candidate_sidecars가 retrieval_visual_cue로 대체하게
# 한다. qwen_bridge_visual_cues가 후보별로 호출한다.
def qwen_bridge_visual_cue(result: QwenBridgeResult) -> VisualCue | None:
    """Return a cue from Qwen's structured safe terms, never from prose fields."""
    if result.status is not QwenBridgeStatus.SUCCESS:
        return None
    confidence = (
        result.confidence
        if result.confidence is not None
        else _DEFAULT_QWEN_CUE_CONFIDENCE
    )
    cue = visual_cue_from_descriptor_terms(
        (*result.selected_terms, *result.extracted_descriptors), confidence
    )
    if cue is None or not is_actionable_visual_cue(cue):
        return None
    if _is_weak_qwen_cue(cue):
        return visual_cue_from_descriptor_terms(
            (*result.selected_terms, *result.extracted_descriptors),
            min(confidence, _WEAK_QWEN_CUE_CONFIDENCE),
        )
    return cue


# Qwen 브리지 결과 맵 전체를 candidate_id별 VisualCue 맵으로 변환한다.
# candidate_sidecars.build_candidate_rag_sidecars가 sources를 만들 때 호출하며,
# 단서를 만들지 못한 후보는 결과 맵에서 그냥 빠진다.
def qwen_bridge_visual_cues(
    results: Mapping[CandidateId, QwenBridgeResult],
) -> Mapping[CandidateId, VisualCue]:
    """Return explicit visual-cue inputs for candidate sidecar construction."""
    cues: dict[CandidateId, VisualCue] = {}
    for candidate_id, result in results.items():
        cue = qwen_bridge_visual_cue(result)
        if cue is not None:
            cues[candidate_id] = cue
    return MappingProxyType(cues)


# morphology/texture가 둘 다 unknown이면(색상만으로 구별되는 경우) 약한
# 단서로 보고, qwen_bridge_visual_cue가 신뢰도를 상한선까지 낮춘다.
def _is_weak_qwen_cue(cue: VisualCue) -> bool:
    return cue.morphology.value == "unknown" and cue.texture_proxy.value == "unknown"
