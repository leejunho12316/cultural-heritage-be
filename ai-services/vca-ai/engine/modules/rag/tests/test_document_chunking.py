from __future__ import annotations

from modules.rag.corpus.document_chunking import chunk_page_text


def test_chunk_page_text_uses_overlapping_sentence_windows() -> None:
    # Given: one extracted PDF page with several conservation observations.
    text = (
        "White powdery accretion appears along the ceramic rim. "
        "The deposit is granular and follows old repair adhesive. "
        "A separate hairline crack crosses the glazed surface. "
        "The crack edge is darkened by trapped soil."
    )

    # When: the page is split for vector indexing.
    chunks = chunk_page_text(text, target_chars=95, max_chars=130, overlap_sentences=1)

    # Then: chunks are smaller than the page and adjacent windows overlap by context.
    assert len(chunks) >= 3
    assert all(len(chunk) <= 130 for chunk in chunks)
    assert chunks[0] != text
    assert chunks[0].split(". ")[-1] in chunks[1]


def test_chunk_page_text_bounds_punctuation_free_pages() -> None:
    # Given: extracted PDF text has no sentence punctuation.
    text = " ".join(f"powder-{index}" for index in range(80))

    # When: the page is split for vector indexing.
    chunks = chunk_page_text(text, target_chars=100, max_chars=140)

    # Then: no emitted chunk can grow into a page-sized vector payload.
    assert len(chunks) > 1
    assert all(len(chunk) <= 140 for chunk in chunks)


def test_chunk_page_text_bounds_single_long_sentence() -> None:
    # Given: one sentence is longer than the hard chunk size.
    text = " ".join(f"efflorescence-{index}" for index in range(60)) + "."

    # When: the page is split for vector indexing.
    chunks = chunk_page_text(text, target_chars=120, max_chars=160)

    # Then: the long sentence is split into bounded windows.
    assert len(chunks) > 1
    assert all(len(chunk) <= 160 for chunk in chunks)
