"""Readers and exact join indexes for candidate RAG artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from modules.rag.qwen.qwen_bridge_json import JsonObject, parse_json_object
from modules.shared import CandidateId
from modules.shared.errors import ContractValidationError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class RoughRagCandidate:
    """Parsed rough masking record with deterministic candidate identity."""

    lane: str
    prompt_text: str
    rough_record_path: str
    rough_record_index: int
    candidate_id: CandidateId


@dataclass(frozen=True, slots=True)
class PromptQueryRecord:
    """Prompt query row keyed by exact lane and prompt text."""

    lane: str
    prompt_text: str
    query_id: str


@dataclass(frozen=True, slots=True)
class PromptRagResultRecord:
    """Prompt RAG result row keyed by query id."""

    query_id: str
    lane: str
    prompt_text: str
    citation_id: str
    chunk_id: str
    score: float
    snippet_text: str
    matched_terms: tuple[str, ...]
    rank: int


type _RoughRecordInput = tuple[Path, str, Path, int, int, str]


def read_rough_records(root: Path) -> tuple[RoughRagCandidate, ...]:
    """Read all rough masking records below a run root in deterministic order."""
    records: list[RoughRagCandidate] = []
    for records_path in sorted(root.glob("**/records.json")):
        lane = records_path.relative_to(root).parts[0]
        accepted_index = 0
        for index, record_text in enumerate(
            _object_texts(records_path.read_text(encoding="utf-8"))
        ):
            record = _rough_record(
                (
                    root,
                    lane,
                    records_path,
                    index,
                    accepted_index,
                    record_text,
                )
            )
            if record is not None:
                records.append(record)
                accepted_index += 1
    return tuple(records)


def read_queries(path: Path) -> tuple[PromptQueryRecord, ...]:
    """Read query JSONL rows that expose lane, prompt text, and query id."""
    rows: list[PromptQueryRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        payload = _json_object(line)
        if payload is None:
            continue
        lane = _string_field(payload, "lane")
        prompt_text = _string_field(payload, "prompt_text")
        query_id = _string_field(payload, "query_id")
        if lane is not None and prompt_text is not None and query_id is not None:
            rows.append(PromptQueryRecord(lane, prompt_text, query_id))
    return tuple(rows)


def read_results(path: Path) -> tuple[PromptRagResultRecord, ...]:
    """Read prompt RAG result JSONL rows without consuming raw page text."""
    rows = tuple(
        row for line in path.read_text(encoding="utf-8").splitlines()
        if (row := _result_record(line))
    )
    return tuple(sorted(rows, key=lambda result: (result.query_id, result.rank)))


def query_index(
    queries: tuple[PromptQueryRecord, ...],
) -> dict[tuple[str, str], tuple[PromptQueryRecord, ...]]:
    """Index queries by exact lane and prompt text, preserving ambiguity."""
    index: dict[tuple[str, str], tuple[PromptQueryRecord, ...]] = {}
    for query in queries:
        key = (query.lane, query.prompt_text)
        index[key] = (*index.get(key, ()), query)
    return index


def result_index(
    results: tuple[PromptRagResultRecord, ...],
) -> dict[str, tuple[PromptRagResultRecord, ...]]:
    """Index retrieval results by query id in rank order."""
    index: dict[str, tuple[PromptRagResultRecord, ...]] = {}
    for result in results:
        index[result.query_id] = (*index.get(result.query_id, ()), result)
    return index


def joined_query(
    rough: RoughRagCandidate,
    queries: Mapping[tuple[str, str], tuple[PromptQueryRecord, ...]],
) -> tuple[PromptQueryRecord | None, str | None]:
    """Return the only exact lane+prompt query match or an explicit reason."""
    matches = queries.get((rough.lane, rough.prompt_text), ())
    if len(matches) == 1:
        return matches[0], None
    reason = "missing_query_join" if not matches else "ambiguous_query_join"
    return None, reason


def joined_results(
    query: PromptQueryRecord | None,
    results: Mapping[str, tuple[PromptRagResultRecord, ...]],
) -> tuple[tuple[PromptRagResultRecord, ...], str | None]:
    """Return query-id retrieval matches or an explicit join reason."""
    if query is None:
        return (), None
    matches = results.get(query.query_id, ())
    if not matches:
        return (), "missing_retrieval_join"
    if any(
        result.lane != query.lane or result.prompt_text != query.prompt_text
        for result in matches
    ):
        return matches, "ambiguous_retrieval_join"
    return matches, None


def _rough_record(input_record: _RoughRecordInput) -> RoughRagCandidate | None:
    root, lane, records_path, raw_index, accepted_index, record_text = input_record
    payload = _json_object(record_text)
    if payload is None or payload.get("accepted") is not True:
        return None
    prompt = _string_field(payload, "prompt")
    image = _string_field(payload, "image")
    if prompt is None or image is None:
        return None
    relative_path = records_path.relative_to(root)
    object_path = "/".join(relative_path.parts[1:-1])
    raw_candidate_id = _string_field(payload, "candidate_id")
    candidate_id = (
        CandidateId(raw_candidate_id)
        if raw_candidate_id is not None and raw_candidate_id.strip()
        else CandidateId(
            f"rough:{lane}:{image}:{object_path}:record-{accepted_index:04d}"
        )
    )
    return RoughRagCandidate(
        lane=lane,
        prompt_text=prompt,
        rough_record_path=str(relative_path),
        rough_record_index=raw_index,
        candidate_id=candidate_id,
    )


def _result_record(line: str) -> PromptRagResultRecord | None:
    payload = _json_object(line)
    if payload is None:
        return None
    query_id = _string_field(payload, "query_id")
    lane = _string_field(payload, "lane")
    prompt_text = _string_field(payload, "prompt_text")
    citation_id = _string_field(payload, "citation_id")
    chunk_id = _string_field(payload, "chunk_id")
    score = _float_field(payload, "score")
    snippet_text = _string_field(payload, "snippet_text")
    rank = _int_field(payload, "rank")
    fields = (
        query_id,
        lane,
        prompt_text,
        citation_id,
        chunk_id,
        score,
        snippet_text,
        rank,
    )
    match fields:
        case (
            str() as query_id,
            str() as lane,
            str() as prompt_text,
            str() as citation_id,
            str() as chunk_id,
            float() as score,
            str() as snippet_text,
            int() as rank,
        ):
            return PromptRagResultRecord(
                query_id=query_id,
                lane=lane,
                prompt_text=prompt_text,
                citation_id=citation_id,
                chunk_id=chunk_id,
                score=score,
                snippet_text=snippet_text,
                matched_terms=_string_array_field(payload, "matched_terms"),
                rank=rank,
            )
        case _:
            return None


def _object_texts(text: str) -> tuple[str, ...]:
    objects: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                objects.append(text[start : index + 1])
    return tuple(objects)


def _json_object(text: str) -> JsonObject | None:
    try:
        return parse_json_object(text)
    except ContractValidationError:
        return None


def _string_field(payload: JsonObject, field: str) -> str | None:
    value = payload.get(field)
    return value if isinstance(value, str) else None


def _string_array_field(payload: JsonObject, field: str) -> tuple[str, ...]:
    value = payload.get(field)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _float_field(payload: JsonObject, field: str) -> float | None:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _int_field(payload: JsonObject, field: str) -> int | None:
    value = payload.get(field)
    return value if isinstance(value, int) and not isinstance(value, bool) else None
