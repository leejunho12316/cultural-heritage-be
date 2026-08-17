"""RAG query-only handoff records derived from Qwen bridge evidence."""

from dataclasses import dataclass

from modules.shared.models import CandidateId


@dataclass(frozen=True, slots=True)
class QwenRagQueryInput:
    """Qwen-derived values allowed to drive RAG retrieval."""

    candidate_id: CandidateId
    selected_terms: tuple[str, ...]
    extracted_descriptors: tuple[str, ...]
