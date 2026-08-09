"""Untrusted Qwen JSON boundary for visual-only observations."""

import math
import re
from typing import Final

from modules.mask_refining.contracts.models import (
    CacheStatus,
    EvidenceContext,
    EvidenceFailure,
    EvidenceFailureCode,
    EvidenceStage,
    QwenEvidenceResult,
    QwenInputView,
    failure_result,
)
from modules.mask_refining.contracts.morphology import normalize_qwen_morphology
from modules.rough_masking.artifacts.records import (
    JsonRecord,
    JsonValue,
    decode_records,
)

_QUERY_FIELD_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9 -]{0,63}$")
# 프롬프트에서 마크다운 코드펜스로 감싸지 말라고 지시하지만, 모델이
# 그래도 종종 감싸서 응답할 때가 있다; 그 이유만으로 실패 처리하지 않고
# 방어적으로 코드펜스를 제거한다.
_JSON_FENCE_PREFIXES: Final = ("```json", "```JSON", "```")


# 이 파일 전역에서 쓰는 실패 결과 생성 헬퍼다. 파싱 과정의 각 검증
# 지점에서 조기 반환할 때 호출되어 실패 사유를 QwenEvidenceResult로 감싼다.
def _failure(
    views: tuple[QwenInputView, ...], code: EvidenceFailureCode, reason: str
) -> QwenEvidenceResult:
    context = EvidenceContext(None, views, CacheStatus.MISS)
    failure = EvidenceFailure(code, reason, EvidenceStage.OBSERVATION_PARSING)
    return failure_result(context, failure)


# JSON 필드가 문자열 배열인지 검증한다. 형식이 맞지 않으면 예외 대신
# None을 반환해 호출자가 MISSING/INVALID 실패로 변환하도록 한다.
def _strings(record: JsonRecord, field: str) -> tuple[str, ...] | None:
    raw = record.get(field)
    if not isinstance(raw, list):
        return None
    values: list[str] = []
    for value in raw:
        if not isinstance(value, str):
            return None
        values.append(value)
    return tuple(values)


# selected_terms/extracted_descriptors처럼 다시 쿼리로 쓰일 문자열
# 필드를 검증한다. 소문자로 정규화하고 _QUERY_FIELD_PATTERN을 벗어나면
# None을 반환해 안전하지 않은 값을 걸러낸다.
def _query_strings(record: JsonRecord, field: str) -> tuple[str, ...] | None:
    values = _strings(record, field)
    if values is None:
        return None
    normalized: list[str] = []
    for value in values:
        term = " ".join(value.lower().split())
        if _QUERY_FIELD_PATTERN.fullmatch(term) is None:
            return None
        normalized.append(term)
    return tuple(normalized)


def _string(record: JsonRecord, field: str) -> str | None:
    raw: JsonValue | None = record.get(field)
    return raw if isinstance(raw, str) and raw.strip() else None


# morphology 필드를 문자열로 확인한 뒤 normalize_qwen_morphology로
# 닫힌 형태 어휘집합에 맞게 정규화한다.
def _morphology(record: JsonRecord) -> str | None:
    raw: JsonValue | None = record.get("morphology")
    if not isinstance(raw, str):
        return None
    return normalize_qwen_morphology(raw)


# Qwen의 원본 출력에서 감싸진 마크다운 코드펜스를 제거해 순수 JSON
# 문자열만 남긴다. parse_observation이 JSON 파싱 전에 호출한다.
def _single_record_payload(raw_output: str) -> str:
    stripped = raw_output.strip()
    if not stripped.endswith("```"):
        return stripped
    for prefix in _JSON_FENCE_PREFIXES:
        if stripped.startswith(prefix):
            return stripped.removeprefix(prefix).removesuffix("```").strip()
    return stripped


def parse_observation(
    raw_output: str, input_views: tuple[QwenInputView, ...]
) -> QwenEvidenceResult:
    """Parse one untrusted Qwen JSON record into success or failure evidence."""
    decoded = decode_records(f"[{_single_record_payload(raw_output)}]")
    if decoded is None or len(decoded) != 1:
        return _failure(
            input_views,
            EvidenceFailureCode.MALFORMED_OUTPUT,
            "json_invalid",
        )
    record = decoded[0]
    observation_id = _string(record, "observation_id")
    observation_text = _string(record, "observation_text")
    reason = _string(record, "reason")
    has_selected_terms = "selected_terms" in record
    has_descriptors = "extracted_descriptors" in record
    has_morphology = "morphology" in record
    selected_terms = _query_strings(record, "selected_terms")
    rejected_terms = _strings(record, "rejected_terms")
    descriptors = _query_strings(record, "extracted_descriptors")
    morphology = _morphology(record)
    if (
        observation_id is None
        or observation_text is None
        or reason is None
        or rejected_terms is None
        or not has_selected_terms
        or not has_descriptors
        or not has_morphology
    ):
        return _failure(
            input_views,
            EvidenceFailureCode.MISSING_OUTPUT_FIELD,
            "required_field_missing",
        )
    if (
        selected_terms is None
        or descriptors is None
        or morphology is None
        or (not selected_terms and not descriptors)
    ):
        return _failure(
            input_views,
            EvidenceFailureCode.INVALID_QUERY_FIELD,
            "query_field_invalid",
        )
    confidence: JsonValue | None = record.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, int | float)
        or not math.isfinite(confidence)
        or confidence < 0
        or confidence > 1
    ):
        return _failure(
            input_views,
            EvidenceFailureCode.INVALID_CONFIDENCE,
            "confidence_invalid",
        )
    return QwenEvidenceResult(
        candidate_id=None,
        input_views=input_views,
        input_view_ids=tuple(view.view_id for view in input_views),
        input_view_hashes=tuple(view.asset_hash for view in input_views),
        cache_status=CacheStatus.MISS,
        observation_id=observation_id,
        observation_text=observation_text,
        selected_terms=selected_terms,
        rejected_terms=rejected_terms,
        extracted_descriptors=descriptors,
        morphology=morphology,
        confidence=float(confidence),
        reason=reason,
        failure_code=None,
        failure_reason=None,
        failed_stage=EvidenceStage.FINALIZED,
        final_success=True,
        report_display_text=observation_text,
    )
