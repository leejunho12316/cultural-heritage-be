"""Page-local text chunking for vector retrieval."""

from __future__ import annotations

from modules.shared import ContractValidationError

DEFAULT_TARGET_CHARS = 2000
DEFAULT_MAX_CHARS = 2600
DEFAULT_OVERLAP_SENTENCES = 1
MIN_TARGET_CHARS = 80


def chunk_page_text(
    text: str,
    *,
    target_chars: int = DEFAULT_TARGET_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_sentences: int = DEFAULT_OVERLAP_SENTENCES,
) -> tuple[str, ...]:
    """Split one extracted PDF page into overlap-preserving evidence chunks."""
    if target_chars < MIN_TARGET_CHARS or max_chars < target_chars:
        field = "chunk_chars"
        reason = "max_chars must be >= target_chars >= 80"
        raise ContractValidationError(field, reason)
    if overlap_sentences < 0:
        field = "overlap_sentences"
        reason = "must be non-negative"
        raise ContractValidationError(field, reason)
    sentences = tuple(
        unit
        for sentence in _sentences(text)
        for unit in _bounded_units(sentence, max_chars)
    )
    if not sentences:
        return ()
    chunks: list[str] = []
    start = 0
    while start < len(sentences):
        end = _window_end(sentences, start, target_chars, max_chars)
        chunks.append(" ".join(sentences[start:end]))
        if end == len(sentences):
            break
        start = max(start + 1, end - overlap_sentences)
    return tuple(chunks)


def _sentences(text: str) -> tuple[str, ...]:
    normalized = " ".join(text.split())
    if not normalized:
        return ()
    parts: list[str] = []
    start = 0
    for index, character in enumerate(normalized):
        if character not in ".!?":
            continue
        sentence = normalized[start : index + 1].strip()
        if sentence:
            parts.append(sentence)
        start = index + 1
    tail = normalized[start:].strip()
    if tail:
        parts.append(tail)
    return tuple(parts) or (normalized,)


def _window_end(
    sentences: tuple[str, ...], start: int, target_chars: int, max_chars: int
) -> int:
    end = start
    total_chars = 0
    while end < len(sentences):
        next_len = len(sentences[end]) + (1 if total_chars else 0)
        if end > start and total_chars + next_len > max_chars:
            break
        total_chars += next_len
        end += 1
        if total_chars >= target_chars:
            break
    return max(start + 1, end)


def _bounded_units(text: str, max_chars: int) -> tuple[str, ...]:
    if len(text) <= max_chars:
        return (text,)
    words = text.split()
    if len(words) <= 1:
        return tuple(
            text[index : index + max_chars]
            for index in range(0, len(text), max_chars)
        )
    units: list[str] = []
    current = ""
    for word in words:
        if not current:
            current = word
            continue
        candidate = f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        units.extend(_bounded_units(current, max_chars))
        current = word
    if current:
        units.extend(_bounded_units(current, max_chars))
    return tuple(units)
