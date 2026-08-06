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
_JSON_FENCE_PREFIXES: Final = ("```json", "```JSON", "```")


def _failure(
    views: tuple[QwenInputView, ...], code: EvidenceFailureCode, reason: str
) -> QwenEvidenceResult:
    context = EvidenceContext(None, views, CacheStatus.MISS)
    failure = EvidenceFailure(code, reason, EvidenceStage.OBSERVATION_PARSING)
    return failure_result(context, failure)


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


def _morphology(record: JsonRecord) -> str | None:
    raw: JsonValue | None = record.get("morphology")
    if not isinstance(raw, str):
        return None
    return normalize_qwen_morphology(raw)


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
