from __future__ import annotations

from pathlib import PurePath

from modules.rag.corpus.corpus import (
    Corpus,
    CorpusDocumentStatus,
    CorpusMetadataRow,
    CorpusPageText,
)
from modules.rag.corpus.document_index import (
    DocumentChunk,
    build_document_index,
    chunks_to_lexical_inputs,
    document_index_records,
    retrieval_snippets_from_chunks,
)
from modules.rag.evidence.citations import (
    NonExportableCorpusCitation,
    adapt_corpus_citation,
)
from modules.rag.retrieval.terms import query_terms


def _corpus() -> Corpus:
    return Corpus.from_metadata(
        (
            CorpusMetadataRow(
                document_id="stone-conservation",
                relative_path="stone.pdf",
                status=CorpusDocumentStatus.INCLUDED_TEXT_PDF.value,
                text=(
                    "White powdery deposit appears on the stone surface. "
                    "The visually distinct crust follows the crack edge. "
                    "Unrelated historical overview continues for many paragraphs."
                ),
                pages=(
                    CorpusPageText(
                        page_number=2,
                        text="White powdery deposit appears on the stone surface.",
                    ),
                    CorpusPageText(
                        page_number=3,
                        text="The visually distinct crust follows the crack edge.",
                    ),
                ),
            ),
            CorpusMetadataRow(
                document_id="blank-scan",
                relative_path="blank.pdf",
                status=CorpusDocumentStatus.EXCLUDED_NO_OCR.value,
                text=None,
            ),
        )
    )


def test_document_index_excludes_garbled_text_records() -> None:
    # Given: a corpus record marked as garbled by a font-encoding mismatch.
    corpus = Corpus.from_metadata(
        (
            CorpusMetadataRow(
                document_id="garbled-symposium",
                relative_path="garbled.pdf",
                status=CorpusDocumentStatus.EXCLUDED_GARBLED_TEXT.value,
                text=None,
            ),
        )
    )

    # When: RAG builds the deterministic document index.
    index = build_document_index(corpus, max_snippet_chars=90)

    # Then: the garbled document contributes no retrievable chunks.
    assert index.chunks == ()


def test_document_index_builds_concise_cited_chunks_for_retrieval() -> None:
    # Given: a validated corpus with one text PDF and one no-OCR PDF.
    corpus = _corpus()

    # When: RAG builds the deterministic document index.
    index = build_document_index(corpus, max_snippet_chars=90)

    # Then: only text PDFs are indexed with stable citation metadata.
    assert len(index.chunks) == 2
    assert index.corpus_id == "document_sweep_260pdf_253text"
    first = index.chunks[0]
    assert isinstance(first, DocumentChunk)
    assert first.document_id == "stone-conservation"
    assert first.relative_path == PurePath("stone.pdf")
    assert first.page_number == 2
    assert first.page_text == "White powdery deposit appears on the stone surface."
    assert first.citation.source_citation == "stone.pdf"
    assert first.citation.page_number == 2
    assert len(first.snippet_text) <= 90


def test_chunks_convert_to_existing_lexical_inputs_without_new_retrieval_contract() -> (
    None
):
    # Given: indexed document chunks.
    index = build_document_index(_corpus(), max_snippet_chars=90)

    # When: chunks are exposed to the existing lexical retrieval contract.
    lexical_inputs = chunks_to_lexical_inputs(index.chunks)

    # Then: retrieval can search chunk snippets while preserving chunk identities.
    assert tuple(item.document_id for item in lexical_inputs) == tuple(
        chunk.chunk_id for chunk in index.chunks
    )
    assert lexical_inputs[0].text == index.chunks[0].snippet_text


def test_retrieval_snippets_from_chunks_preserve_citations_for_prompt_refinement() -> (
    None
):
    # Given: chunks with visual terms useful for rough-mask refinement.
    index = build_document_index(_corpus(), max_snippet_chars=90)
    terms = query_terms((), ("white powdery deposit", "crack edge"))

    # When: document index retrieval returns RAG snippets.
    snippets = retrieval_snippets_from_chunks(index.chunks, terms, top_k=2)

    # Then: visually grounded evidence is ranked with citation metadata intact.
    assert tuple(snippet.matched_terms for snippet in snippets) == (
        ("white powdery deposit",),
        ("crack edge",),
    )
    assert snippets[0].citation.citation_id == index.chunks[0].citation.citation_id
    assert snippets[0].citation.page_number == 2
    assert "historical overview" not in snippets[0].snippet_text


def test_document_index_records_are_jsonl_ready_and_cited() -> None:
    # Given: a deterministic document index.
    index = build_document_index(_corpus(), max_snippet_chars=90)

    # When: index chunks are converted to materialized JSONL records.
    records = document_index_records(index)

    # Then: records retain prompt-refine snippet and citation fields.
    assert records[0]["corpus_id"] == "document_sweep_260pdf_253text"
    assert records[0]["document_id"] == "stone-conservation"
    assert records[0]["page_number"] == 2
    assert records[0]["page_text"] == index.chunks[0].page_text
    assert records[0]["source_citation"] == "stone.pdf"
    assert records[0]["snippet_text"] == index.chunks[0].snippet_text


def test_document_index_keeps_legacy_whole_document_text_non_exportable() -> None:
    # Given: metadata from a legacy adapter with no page-level provenance.
    corpus = Corpus.from_metadata(
        (
            CorpusMetadataRow(
                document_id="legacy-stone",
                relative_path="legacy.pdf",
                status=CorpusDocumentStatus.INCLUDED_TEXT_PDF.value,
                text="White powdery deposit appears on the stone surface.",
            ),
        )
    )

    # When: RAG indexes the legacy whole-document text.
    index = build_document_index(corpus, max_snippet_chars=90)
    adapted = adapt_corpus_citation(index.chunks[0].citation)

    # Then: unknown page provenance remains non-exportable.
    assert index.chunks[0].page_number is None
    assert index.chunks[0].page_text == ""
    assert isinstance(adapted, NonExportableCorpusCitation)
