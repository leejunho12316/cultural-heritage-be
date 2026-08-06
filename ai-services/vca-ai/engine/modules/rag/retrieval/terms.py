"""Source-backed lexical query terms for local RAG retrieval."""

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import NoReturn

from modules.shared import ContractValidationError


@unique
class ObservedTermSource(StrEnum):
    """Allowed provenance sources for observed retrieval terms."""

    SNIPPET = "snippet"
    MANIFEST_TITLE = "manifest_title"
    EXTRACTION_METADATA = "extraction_metadata"
    MAPPING_TABLE = "mapping_table"


@dataclass(frozen=True, slots=True)
class RetrievalTerm:
    """A normalized retrieval term with explicit source provenance."""

    term: str
    source: ObservedTermSource
    language: str

    def __post_init__(self) -> None:
        """Reject unsupported term metadata at construction."""
        if not self.term.strip():
            _raise_contract("term", "must not be blank")
        match self.language:
            case "ko" | "en":
                return
            case _:
                _raise_contract("language", "must be ko or en")


@dataclass(frozen=True, slots=True)
class QueryTerms:
    """Deterministic lexical query surface for retrieval."""

    observed_terms: tuple[RetrievalTerm, ...]
    english_terms: tuple[str, ...]

    def __post_init__(self) -> None:
        """Reject empty or blank query surfaces."""
        if not self.observed_terms and not self.english_terms:
            _raise_contract("query_terms", "must not be empty")
        if any(not term.strip() for term in self.english_terms):
            _raise_contract("english_terms", "must not contain blanks")

    @property
    def lexical_tokens(self) -> tuple[str, ...]:
        """Return case-folded tokens in stable first-seen order."""
        tokens = [term.term.casefold() for term in self.observed_terms]
        tokens.extend(term.casefold() for term in self.english_terms)
        return tuple(dict.fromkeys(tokens))


def observed_korean_term(term: str, source: ObservedTermSource) -> RetrievalTerm:
    """Create a provenance-backed Korean retrieval term."""
    return RetrievalTerm(term=term, source=source, language="ko")


def observed_english_term(term: str, source: ObservedTermSource) -> RetrievalTerm:
    """Create a provenance-backed English retrieval term."""
    return RetrievalTerm(term=term, source=source, language="en")


def query_terms(
    observed_terms: tuple[RetrievalTerm, ...],
    english_terms: tuple[str, ...],
) -> QueryTerms:
    """Create validated query terms without translation or invented terms."""
    return QueryTerms(observed_terms=observed_terms, english_terms=english_terms)


def _raise_contract(field: str, reason: str) -> NoReturn:
    raise ContractValidationError(field, reason)
