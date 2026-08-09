from __future__ import annotations

from pathlib import PurePath

from modules.rag.corpus.corpus import CorpusDocumentId
from modules.rag.corpus.document_index import DocumentChunk
from modules.rag.evidence.citations import ChunkId, CitationId, CorpusCitation
from modules.rag.retrieval.vector_index import (
    InMemoryTextEmbedder,
    build_vector_index,
    vector_retrieve,
)


def _chunk(chunk_id: str, text: str) -> DocumentChunk:
    return DocumentChunk(
        chunk_id=ChunkId(chunk_id),
        document_id=CorpusDocumentId("stone-care"),
        relative_path=PurePath("stone.pdf"),
        page_number=4,
        page_text=text,
        snippet_text=text,
        citation=CorpusCitation(
            citation_id=CitationId(f"{chunk_id}:citation"),
            chunk_id=ChunkId(chunk_id),
            source_citation="stone.pdf",
            source_type="corpus_pdf",
            license_status="internal_review",
            title="stone.pdf",
            score=0.0,
            page_number=4,
        ),
    )


def test_vector_retrieval_returns_semantic_match_without_lexical_overlap() -> None:
    # Given: embeddings encode semantic similarity without shared query words.
    embedder = InMemoryTextEmbedder(
        passage_vectors={
            "white powdery accretion on stone": (1.0, 0.0),
            "dark structural fracture in glaze": (0.0, 1.0),
        },
        query_vectors={
            "salt efflorescence": (1.0, 0.0),
        },
        model_id="test-embedder",
    )
    index = build_vector_index(
        (
            _chunk("chunk-1", "white powdery accretion on stone"),
            _chunk("chunk-2", "dark structural fracture in glaze"),
        ),
        embedder,
    )

    # When: vector retrieval searches a semantically related query.
    snippets = vector_retrieve(index, "salt efflorescence", embedder, top_k=1)

    # Then: the closest semantic chunk is returned despite no lexical overlap.
    assert snippets[0].snippet_text == "white powdery accretion on stone"
    assert snippets[0].matched_terms == ()
    assert snippets[0].citation.page_number == 4
