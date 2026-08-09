from __future__ import annotations

import pytest

from modules.rag.retrieval.terms import (
    ObservedTermSource,
    QueryTerms,
    observed_english_term,
    observed_korean_term,
    query_terms,
)
from modules.shared import ContractValidationError


def test_query_terms_preserve_source_backed_korean_and_english_terms() -> None:
    # Given: observed corpus terms with explicit allowed provenance.
    korean = observed_korean_term("백색 침전물", ObservedTermSource.SNIPPET)
    english = observed_english_term("white deposit", ObservedTermSource.MAPPING_TABLE)

    # When: query terms are normalized for lexical retrieval.
    terms = query_terms((korean, english), ("deposit", "artifact surface"))

    # Then: all search tokens are deterministic and source-backed.
    assert terms.lexical_tokens == (
        "백색 침전물",
        "white deposit",
        "deposit",
        "artifact surface",
    )
    assert korean.source is ObservedTermSource.SNIPPET
    assert english.source is ObservedTermSource.MAPPING_TABLE


def test_query_terms_deduplicate_casefolded_tokens_without_translation() -> None:
    # Given: repeated English terms from observed and normalized sources.
    observed = observed_english_term("Crack", ObservedTermSource.MANIFEST_TITLE)

    # When: lexical tokens are prepared.
    terms = query_terms((observed,), ("crack", "thin line"))

    # Then: duplicate casing is collapsed without adding invented translations.
    assert terms.lexical_tokens == ("crack", "thin line")


def test_source_less_or_blank_terms_are_rejected() -> None:
    # Given: malformed term inputs at the retrieval boundary.
    # When/Then: source-backed query construction fails closed.
    with pytest.raises(ContractValidationError, match="term"):
        _ = observed_korean_term("", ObservedTermSource.SNIPPET)
    with pytest.raises(ContractValidationError, match="english_terms"):
        _ = QueryTerms(
            observed_terms=(
                observed_korean_term("백색 침전물", ObservedTermSource.SNIPPET),
            ),
            english_terms=("",),
        )


def test_empty_query_terms_are_rejected() -> None:
    # Given: no observed or English normalized term.
    # When/Then: retrieval cannot proceed with an empty query surface.
    with pytest.raises(ContractValidationError, match="query_terms"):
        _ = query_terms((), ())
