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
    image_id: str


@dataclass(frozen=True, slots=True)
class PromptQueryRecord:
    """Prompt query row keyed by exact lane, prompt text, and Qwen signature."""

    lane: str
    prompt_text: str
    query_id: str
    # 후보 고유 Qwen 서술어 시그니처(candidate_card_terms.qwen_query_signature가
    # 계산). 비어 있으면(Qwen 데이터 없음/실패) 예전과 동일하게 (lane,
    # prompt_text)만으로 조인된다 - 기존 JSONL 픽스처가 이 필드 없이도 그대로
    # 파싱되도록 기본값을 둔다.
    qwen_signature: tuple[str, ...] = ()


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


# rough masking 산출물(records.json)들을 순회하며 채택된(accepted) 레코드만
# 후보로 파싱한다. startup_runner._materialize_missing_retrieval_artifacts와
# candidate_sidecars.build_candidate_rag_sidecars 양쪽에서 호출된다.
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


# queries.jsonl을 읽어 쿼리 레코드로 복원한다. qwen_signature 필드는 예전
# 산출물에는 없을 수 있어 _string_array_field가 없으면 빈 튜플로 처리한다.
# candidate_sidecars.build_candidate_rag_sidecars가 query_index와 함께 호출한다.
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
            qwen_signature = _string_array_field(payload, "qwen_signature")
            rows.append(PromptQueryRecord(lane, prompt_text, query_id, qwen_signature))
    return tuple(rows)


# prompt_rag_results.jsonl을 읽어 검색 결과 레코드로 복원한다. 반환값은
# (query_id, rank) 순으로 정렬되어 있어 호출부가 다시 정렬할 필요가 없다.
def read_results(path: Path) -> tuple[PromptRagResultRecord, ...]:
    """Read prompt RAG result JSONL rows without consuming raw page text."""
    rows = tuple(
        row for line in path.read_text(encoding="utf-8").splitlines()
        if (row := _result_record(line))
    )
    return tuple(sorted(rows, key=lambda result: (result.query_id, result.rank)))


# 쿼리 목록을 (lane, prompt_text, qwen_signature) 키로 인덱싱한다. 같은 키에
# 여러 쿼리가 있는 모호한 상황도 그대로 보존해 joined_query가 명시적으로
# 판단하게 한다.
def query_index(
    queries: tuple[PromptQueryRecord, ...],
) -> dict[tuple[str, str, tuple[str, ...]], tuple[PromptQueryRecord, ...]]:
    """Index queries by exact lane, prompt text, and Qwen signature."""
    index: dict[tuple[str, str, tuple[str, ...]], tuple[PromptQueryRecord, ...]] = {}
    for query in queries:
        key = (query.lane, query.prompt_text, query.qwen_signature)
        index[key] = (*index.get(key, ()), query)
    return index


# 검색 결과를 query_id 기준으로 인덱싱한다(순위 순서 유지).
# joined_results가 조인된 쿼리의 결과를 찾을 때 사용한다.
def result_index(
    results: tuple[PromptRagResultRecord, ...],
) -> dict[str, tuple[PromptRagResultRecord, ...]]:
    """Index retrieval results by query id in rank order."""
    index: dict[str, tuple[PromptRagResultRecord, ...]] = {}
    for result in results:
        index[result.query_id] = (*index.get(result.query_id, ()), result)
    return index


# rough 후보 하나를 lane+prompt_text+qwen_signature가 정확히 일치하는 쿼리
# 레코드와 조인한다. candidate_sidecars._build_candidate에서 호출되며, 매칭이
# 없거나(missing) 여러 개면(ambiguous) None과 사유 문자열을 함께 돌려준다.
def joined_query(
    rough: RoughRagCandidate,
    qwen_signature: tuple[str, ...],
    queries: Mapping[tuple[str, str, tuple[str, ...]], tuple[PromptQueryRecord, ...]],
) -> tuple[PromptQueryRecord | None, str | None]:
    """Return the only exact lane+prompt+signature query match or a reason."""
    # 이 (lane, prompt_text, qwen_signature) 키는 startup_runner._prompt_queries의
    # 중복 제거 키와 동일하다. qwen_signature가 비어 있으면(Qwen 데이터 없음/
    # 실패/스킵 모드) 같은 시드 프롬프트를 쓰는 후보들이 예전처럼 하나의
    # query_id로 조인되어 검색 결과를 공유한다 - 하지만 서로 다른 Qwen
    # 서술어를 가진 후보들은 이제 각자 다른 query_id로 조인된다.
    matches = queries.get((rough.lane, rough.prompt_text, qwen_signature), ())
    if len(matches) == 1:
        return matches[0], None
    reason = "missing_query_join" if not matches else "ambiguous_query_join"
    return None, reason


# joined_query가 찾아준 쿼리의 query_id로 실제 검색 결과들을 가져온다.
# candidate_sidecars._build_candidate에서 joined_query 다음 단계로 호출되며,
# lane/prompt_text가 쿼리와 어긋나는 결과가 섞여 있으면 ambiguous로 표시한다.
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


# JSON 텍스트 한 덩어리를 rough 후보 하나로 파싱한다. accepted가 True가 아니거나
# 필수 필드(prompt, image)가 없으면 None을 돌려줘 read_rough_records가 해당
# 레코드를 건너뛰게 한다.
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
        image_id=image,
    )


# JSONL 한 줄을 검색 결과 레코드로 파싱한다. match 문으로 모든 필드 타입을 한
# 번에 검증하며, 하나라도 어긋나면 None을 반환해 read_results가 해당 줄을
# 건너뛰게 한다.
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


# records.json이 표준 JSON 배열이 아니라 최상위 JSON 객체들이 연속으로 이어진
# 형식이라, 문자열/이스케이프를 고려해 중괄호 깊이를 직접 추적하며 객체 단위로
# 잘라낸다. read_rough_records가 각 객체를 개별 레코드로 파싱하기 전에 호출한다.
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
