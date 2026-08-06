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


def _is_weak_qwen_cue(cue: VisualCue) -> bool:
    return cue.morphology.value == "unknown" and cue.texture_proxy.value == "unknown"
