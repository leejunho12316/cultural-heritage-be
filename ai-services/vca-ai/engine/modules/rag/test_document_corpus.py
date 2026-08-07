from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from modules.rag.corpus.document_corpus import discover_document_pdfs
from modules.shared import PathSafetyError

if TYPE_CHECKING:
    from pathlib import Path


def test_discover_document_pdfs_rejects_symlinked_pdf_leaf(tmp_path: Path) -> None:
    # Given: a PDF-looking source entry is a symlink outside the corpus root.
    source_root = tmp_path / "document"
    source_root.mkdir()
    outside_pdf = tmp_path / "outside.pdf"
    _ = outside_pdf.write_bytes(b"%PDF outside")
    (source_root / "linked.pdf").symlink_to(outside_pdf)

    # When/Then: discovery rejects the symlink before extraction can read it.
    with pytest.raises(PathSafetyError):
        _ = discover_document_pdfs(source_root)
