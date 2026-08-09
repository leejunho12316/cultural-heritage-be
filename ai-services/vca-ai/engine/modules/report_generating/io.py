"""Strict JSON boundary for report generation inputs and receipts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final, NoReturn

from modules.report_generating.json_parser import parse_json_object
from modules.report_generating.models import (
    REPORT_GENERATING_REQUEST_SCHEMA,
    TRACE_SOURCE_SCHEMA,
    CitationRecord,
    ImageSummary,
    JsonObject,
    JsonValue,
    NoFakeClaimAudit,
    ReportGeneratingRequest,
    RunSummary,
    TraceCandidate,
    TraceCandidateBbox,
    TraceSource,
)
from modules.shared import ContractValidationError

_POLYGON_POINT_COORDINATES: Final = 2


# runner.py의 CLI main이 호출하는 독립 실행 요청 파일 읽기 진입점.
def read_request(path: Path) -> ReportGeneratingRequest:
    """Read a standalone report-generation request."""
    return parse_request(read_json_object(path))


# read_request가 호출하는 실제 파싱 로직. 스키마 버전을 검증한 뒤 파일시스템
# 경로 3개(workspace_root/run_root/trace_source_path)로 변환한다.
def parse_request(payload: JsonObject) -> ReportGeneratingRequest:
    """Parse an untrusted request into typed filesystem inputs."""
    if _string(payload, "schema") != REPORT_GENERATING_REQUEST_SCHEMA:
        _invalid("schema", "unsupported report generation request")
    return ReportGeneratingRequest(
        Path(_string(payload, "workspace_root")),
        Path(_string(payload, "run_root")),
        Path(_string(payload, "trace_source_path")),
    )


# runner.py의 run_report_generation이 anomaly_grouping이 남긴
# report_trace_source.json을 읽을 때 호출한다.
def read_trace_source(path: Path) -> TraceSource:
    """Read and parse a trace source document at the trust boundary."""
    return parse_trace_source(read_json_object(path))


# read_trace_source와 final.py의 _source_from_metadata 양쪽에서 호출되는 핵심
# 파싱 함수. 신뢰할 수 없는 JSON 경계에서 스키마를 검증하고, 내부 전용
# CorpusCitation 레코드가 새어 들어오면 거부한다.
def parse_trace_source(payload: JsonObject) -> TraceSource:
    """Parse report-safe evidence and reject internal citation records."""
    if _string(payload, "schema") != TRACE_SOURCE_SCHEMA:
        _invalid("schema", "unsupported trace source")
    # 후보 순서는 trace source payload에 나타난 그대로 유지되며 재정렬하지 않는다:
    # 하위 소비자(FE)가 배열 위치로 finding 번호를 매기므로, 여기서 순서를
    # 바꾸면 조용히 번호가 뒤바뀐다.
    return TraceSource(
        _run_summary(_object(payload, "run_summary")),
        _claim_audit(_object(payload, "no_fake_claim_audit")),
        tuple(_image(value) for value in _objects(payload, "images")),
        tuple(_candidate(value) for value in _objects(payload, "candidates")),
        tuple(
            _string_value(value, "relations")
            for value in _values(payload, "relations")
        ),
        _object(payload, "budget"),
        _object_or_empty(payload, "scale_metadata"),
        _object_or_empty(payload, "tile_metadata"),
    )


# 이 모듈과 verification 모듈들이 공유하는 저수준 파일 읽기 헬퍼.
def read_json_object(path: Path) -> JsonObject:
    """Read a JSON object without allowing loose values past the boundary."""
    return parse_json_object(path.read_text(encoding="utf-8"))


# 이 모듈과 trace.py/final.py/verification 모듈들이 공유하는 저수준 파일 쓰기
# 헬퍼.
def write_json(path: Path, payload: JsonObject) -> None:
    """Write deterministic UTF-8 JSON after parent creation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _run_summary(payload: JsonObject) -> RunSummary:
    return RunSummary(_string(payload, "status"), _bool(payload, "final_success"))


def _claim_audit(payload: JsonObject) -> NoFakeClaimAudit:
    return NoFakeClaimAudit(
        _string(payload, "status"),
        _nonnegative_int(payload, "claimed_candidate_count"),
        _nonnegative_int(payload, "fabricated_candidate_count"),
        _bool(payload, "runner_invoked"),
    )


def _image(payload: JsonObject) -> ImageSummary:
    return ImageSummary(_string(payload, "image_id"), _string(payload, "summary"))


# parse_trace_source가 candidates 배열의 항목마다 호출한다. 후보 하나의 trace
# 표현을 TraceCandidate로 변환한다.
def _candidate(payload: JsonObject) -> TraceCandidate:
    return TraceCandidate(
        _safe_identifier(_string(payload, "candidate_id"), "candidate_id"),
        _string(payload, "image_id"),
        _bool(payload, "final_success"),
        # 아래 followup_mode와 달리 여기서는 닫힌 값 집합으로 재검증하지 않는다:
        # 허용 어휘(prompt_generating.VisualConceptFamily)는 상류에서 이미
        # 강제되지만, FE의 한글 라벨 조회가 이 값을 그대로 키로 사용하므로
        # 전체 파이프라인에서 이 어휘를 벗어나면 안 된다.
        _string(payload, "concept_family"),
        _string(payload, "hybrid_descriptor"),
        _followup_mode(payload),
        _string(payload, "followup_reason"),
        _string(payload, "trigger_priority"),
        _string(payload, "selected_parent_target_type"),
        _string(payload, "selected_parent_target_id"),
        _string(payload, "terminal_status"),
        _string(payload, "relation_authority_outcome"),
        _string(payload, "duplicate_suppression_key"),
        tuple(_citation(value) for value in _objects(payload, "citations")),
        _object_or_empty(payload, "rag_query"),
        _objects_or_empty(payload, "generated_prompts"),
        _object_or_empty(payload, "reopen"),
        _objects_or_empty(payload, "coverage_metrics"),
        _nullable_string(payload, "skip_reason"),
        _bbox(payload, "bbox"),
        _polygon(payload, "polygon"),
        _nullable_bool(payload, "qwen_final_success"),
        _nullable_string(payload, "qwen_report_display_text"),
        _nullable_nonnegative_float(payload, "qwen_confidence"),
    )


# _candidate에서 선택적 bbox 필드를 파싱한다.
def _bbox(payload: JsonObject, field: str) -> TraceCandidateBbox | None:
    value = payload.get(field)
    if value is None:
        return None
    bbox_payload = _json_object(value, field)
    return TraceCandidateBbox(
        _nonnegative_float(bbox_payload, "x_min"),
        _nonnegative_float(bbox_payload, "y_min"),
        _nonnegative_float(bbox_payload, "x_max"),
        _nonnegative_float(bbox_payload, "y_max"),
    )


# _candidate에서 선택적 polygon 필드를 파싱한다. 각 점은 [x, y] 2요소 배열.
def _polygon(
    payload: JsonObject, field: str
) -> tuple[tuple[float, float], ...] | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, list):
        _invalid(field, "must be a list of [x, y] points")
    points: list[tuple[float, float]] = []
    for item in value:
        if not isinstance(item, list) or len(item) != _POLYGON_POINT_COORDINATES:
            _invalid(field, "must be a list of [x, y] points")
        x_value, y_value = item
        if isinstance(x_value, bool) or isinstance(y_value, bool):
            _invalid(field, "must be a list of [x, y] points")
        if not isinstance(x_value, (float, int)) or not isinstance(
            y_value, (float, int)
        ):
            _invalid(field, "must be a list of [x, y] points")
        points.append((float(x_value), float(y_value)))
    return tuple(points)


# _candidate에서 인용마다 호출된다. status가 "exported"면 page_number를
# 포함한 전체 필드를, "non_exportable_corpus_citation"이면 citation_id만
# 남긴 축소 레코드를 만든다. 내부 전용 CorpusCitation record_type은 거부한다.
def _citation(payload: JsonObject) -> CitationRecord:
    record_type = payload.get("record_type")
    if record_type == "CorpusCitation":
        _invalid("citations", "internal CorpusCitation records are not report-safe")
    status = _string(payload, "status")
    citation_id = _string(payload, "citation_id")
    if status == "exported":
        page_number = _positive_int(payload, "page_number")
        return CitationRecord(
            status,
            citation_id,
            _nullable_string(payload, "chunk_id"),
            _nullable_string(payload, "source_citation"),
            _nullable_string(payload, "source_type"),
            _nullable_string(payload, "license_status"),
            _string(payload, "title"),
            _nullable_nonnegative_float(payload, "score"),
            page_number,
        )
    if status == "non_exportable_corpus_citation":
        return CitationRecord(
            status, citation_id, None, None, None, None, None, None, None
        )
    return _invalid("citations.status", "must be an adapter export status")


# _candidate에서 followup_mode가 닫힌 값 집합
# ("automatic"/"user_requested") 안에 있는지 검증한다.
def _followup_mode(payload: JsonObject) -> str:
    mode = _string(payload, "followup_mode")
    if mode not in {"automatic", "user_requested"}:
        _invalid("followup_mode", "must be automatic or user_requested")
    return mode


def _object(payload: JsonObject, field: str) -> JsonObject:
    return _json_object(payload.get(field), field)


def _object_or_empty(payload: JsonObject, field: str) -> JsonObject:
    value = payload.get(field)
    return {} if value is None else _json_object(value, field)


def _objects(payload: JsonObject, field: str) -> tuple[JsonObject, ...]:
    return tuple(_json_object(value, field) for value in _values(payload, field))


def _objects_or_empty(payload: JsonObject, field: str) -> tuple[JsonObject, ...]:
    value = payload.get(field)
    if value is None:
        return ()
    if not isinstance(value, list):
        _invalid(field, "must be a list")
    return tuple(_json_object(item, field) for item in value)


def _values(payload: JsonObject, field: str) -> list[JsonValue]:
    value = payload.get(field)
    if not isinstance(value, list):
        _invalid(field, "must be a list")
    return value


def _string(payload: JsonObject, field: str) -> str:
    return _string_value(payload.get(field), field)


def _string_value(value: JsonValue | None, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid(field, "must be a nonblank string")
    return value


def _bool(payload: JsonObject, field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        _invalid(field, "must be a boolean")
    return value


def _nullable_string(payload: JsonObject, field: str) -> str | None:
    value = payload.get(field)
    return None if value is None else _string_value(value, field)


def _nullable_bool(payload: JsonObject, field: str) -> bool | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, bool):
        _invalid(field, "must be a boolean or null")
    return value


def _nonnegative_int(payload: JsonObject, field: str) -> int:
    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        _invalid(field, "must be a non-negative integer")
    return value


def _positive_int(payload: JsonObject, field: str) -> int:
    value = _nonnegative_int(payload, field)
    if value < 1:
        _invalid(field, "must be a positive integer")
    return value


def _nonnegative_float(payload: JsonObject, field: str) -> float:
    value = payload.get(field)
    if (
        not isinstance(value, (float, int))
        or isinstance(value, bool)
        or value < 0
    ):
        _invalid(field, "must be a non-negative number")
    return float(value)


def _nullable_nonnegative_float(payload: JsonObject, field: str) -> float | None:
    value = payload.get(field)
    return None if value is None else _nonnegative_float(payload, field)


def _json_object(value: JsonValue, field: str = "document") -> JsonObject:
    if not isinstance(value, dict):
        _invalid(field, "must be a JSON object")
    return value


def _safe_identifier(value: str, field: str) -> str:
    if "/" in value or "\\" in value or value in {".", ".."}:
        _invalid(field, "must be a safe path segment")
    return value


def _invalid(field: str, reason: str) -> NoReturn:
    raise ContractValidationError(field, reason)
