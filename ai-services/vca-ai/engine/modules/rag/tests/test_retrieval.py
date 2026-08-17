from __future__ import annotations

from pathlib import PurePath

import pytest

from modules.rag.corpus.corpus import CorpusDocumentId, LexicalDocumentInput
from modules.rag.retrieval.retrieval import lexical_retrieve
from modules.rag.retrieval.terms import (
    ObservedTermSource,
    observed_korean_term,
    query_terms,
)
from modules.shared import ContractValidationError


def test_lexical_retrieval_returns_stable_top_k_citation_evidence() -> None:
    # Given: text-bearing corpus inputs and source-backed observed query terms.
    documents = (
        LexicalDocumentInput(
            document_id=CorpusDocumentId("doc-b"),
            relative_path=PurePath("b.pdf"),
            text="백색 침전물 with white deposit on the surface.",
        ),
        LexicalDocumentInput(
            document_id=CorpusDocumentId("doc-a"),
            relative_path=PurePath("a.pdf"),
            text="white deposit visible near rim. white deposit remains stable.",
        ),
        LexicalDocumentInput(
            document_id=CorpusDocumentId("doc-c"),
            relative_path=PurePath("c.pdf"),
            text="unrelated conservation note",
        ),
    )
    terms = query_terms(
        (observed_korean_term("백색 침전물", ObservedTermSource.SNIPPET),),
        ("white deposit",),
    )

    # When: local lexical retrieval runs with a top-k limit.
    result = lexical_retrieve(documents, terms, top_k=2)

    # Then: ordering is score-first and deterministic across tied source order.
    assert tuple(snippet.document_id for snippet in result.snippets) == (
        CorpusDocumentId("doc-a"),
        CorpusDocumentId("doc-b"),
    )
    assert result.snippets[0].citation.source_citation == "a.pdf"
    assert result.snippets[0].matched_terms == ("white deposit",)
    assert result.snippets[1].matched_terms == ("백색 침전물", "white deposit")


def test_retrieval_coverage_metrics_include_terminal_rates() -> None:
    # Given: one target corpus with one lexical cue and one no-cue document.
    documents = (
        LexicalDocumentInput(
            document_id=CorpusDocumentId("doc-a"),
            relative_path=PurePath("a.pdf"),
            text="thin crack follows the glaze boundary",
        ),
        LexicalDocumentInput(
            document_id=CorpusDocumentId("doc-b"),
            relative_path=PurePath("b.pdf"),
            text="general catalog metadata",
        ),
    )
    terms = query_terms((), ("thin crack",))

    # When: retrieval evaluates the local corpus.
    result = lexical_retrieve(documents, terms, top_k=5)

    # Then: valid target coverage reports both positive and terminal rates.
    metrics = {metric.name: metric.value for metric in result.coverage_metrics}
    assert metrics == {
        "citation_coverage": 0.5,
        "visual_cue_coverage": 0.5,
        "no_citation_rate": 0.5,
        "no_visual_cue_rate": 0.5,
    }


def test_retrieval_exposes_no_executable_prompt_text_or_external_seams() -> None:
    # Given: a deterministic local retrieval result.
    result = lexical_retrieve(
        (
            LexicalDocumentInput(
                document_id=CorpusDocumentId("doc-a"),
                relative_path=PurePath("a.pdf"),
                text="localized stain near repair edge",
            ),
        ),
        query_terms((), ("localized stain",)),
        top_k=1,
    )

    # When/Then: snippets are report evidence, not prompt material.
    assert not hasattr(result.snippets[0], "prompt_text")
    assert "localized stain" in result.snippets[0].snippet_text


def test_invalid_top_k_is_rejected() -> None:
    # Given: a top-k value outside the retrieval contract.
    # When/Then: construction fails before retrieval can run.
    with pytest.raises(ContractValidationError, match="top_k"):
        _ = lexical_retrieve((), query_terms((), ("crack",)), top_k=0)
