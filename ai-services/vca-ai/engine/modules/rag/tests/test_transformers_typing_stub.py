from __future__ import annotations

from pathlib import Path


def test_transformers_typing_stub_is_self_contained() -> None:
    # Given: the local third-party Transformers stub is used by multiple modules.
    stub = "typings/transformers/__init__.pyi"

    # When: the stub source is inspected.
    source = Path(stub).read_text(encoding="utf-8")

    # Then: it does not depend on application module layout.
    assert "from modules." not in source
