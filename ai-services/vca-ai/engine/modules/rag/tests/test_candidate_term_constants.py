from __future__ import annotations

from modules import prompt_generating
from modules.rag.operations import candidate_term_constants


# rag/SESSION_BRIEF.md: "RAG must not implement a second allowlist, banned-term
# list, or lane prompt renderer" - prompt_generating owns the one allowlist
# that gates BOTH card display terms and prompt-safety checks. A second,
# independently-maintained copy here previously drifted out of sync (RAG's
# copy got 11 words added for card richness without prompt_generating's
# safety-check copy getting the same words), which crashed the RAG stage in
# production with PromptSafetyError the first time a real candidate's
# descriptor terms used one of the new words. These tests pin the re-export
# so the two can never diverge again.
def test_allowed_descriptor_terms_is_the_shared_prompt_generating_object() -> None:
    assert (
        candidate_term_constants.ALLOWED_DESCRIPTOR_TERMS
        is prompt_generating.ALLOWED_DESCRIPTOR_TERMS
    )


def test_allowed_material_terms_is_the_shared_prompt_generating_object() -> None:
    assert (
        candidate_term_constants.ALLOWED_MATERIAL_TERMS
        is prompt_generating.ALLOWED_MATERIAL_TERMS
    )


def test_allowed_context_terms_is_the_shared_prompt_generating_object() -> None:
    assert (
        candidate_term_constants.ALLOWED_CONTEXT_TERMS
        is prompt_generating.ALLOWED_CONTEXT_TERMS
    )
