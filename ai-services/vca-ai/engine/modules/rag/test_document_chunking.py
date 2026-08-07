from __future__ import annotations

from modules.rag.corpus.document_chunking import chunk_page_text


def test_chunk_page_text_bounds_punctuation_free_pages() -> None:
    # Given: extracted PDF text has no sentence punctuation.
    text = " ".join(f"powder-{index}" for index in range(80))

    # When: the page is split for vector indexing.
    chunks = chunk_page_text(text, target_chars=100, max_chars=140)

    # Then: no emitted chunk can grow into a page-sized vector payload.
    assert len(chunks) > 1
    assert all(len(chunk) <= 140 for chunk in chunks)
