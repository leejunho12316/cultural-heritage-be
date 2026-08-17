"""Evidence-only final report generation from verified trace artifacts."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from modules.report_generating.io import (
    parse_trace_source,
    read_json_object,
    write_json,
)
from modules.report_generating.models import (
    FINAL_METADATA_SCHEMA,
    TRACE_SOURCE_SCHEMA,
    JsonObject,
    JsonValue,
    TraceSource,
)
from modules.report_generating.rendering import final_index_html
from modules.report_generating.verification import trace_receipt_integrity_error
from modules.shared import ContractValidationError, ensure_final_report_write_paths

if TYPE_CHECKING:
    from pathlib import Path


# runner.py의 run_report_generation이 trace 검증 통과 후에 호출하는 최종
# 한국어 리포트 생성 진입점. trace 영수증 무결성이 깨지면 예외를 던지고 아무것도
# 쓰지 않는다.
def generate_final_report(run_root: Path) -> Path:
    """Write Korean evidence-only output only after trace receipt integrity passes."""
    trace_error = trace_receipt_integrity_error(run_root)
    if trace_error is not None:
        field = "trace_verification"
        raise ContractValidationError(field, trace_error)
    report_root = run_root / "report"
    metadata_path = report_root / "metadata.json"
    source = _source_from_metadata(read_json_object(metadata_path))
    final_root = run_root / "final_report"
    index_path, final_metadata_path = ensure_final_report_write_paths(
        run_root, final_root
    )
    final_root.mkdir(parents=True, exist_ok=True)
    _ = index_path.write_text(final_index_html(source), encoding="utf-8")
    write_json(
        final_metadata_path,
        {
            "schema": FINAL_METADATA_SCHEMA,
            "run_root": str(run_root.resolve()),
            "trace_index_path": "../report/index.html",
            "trace_metadata_sha256": _sha256(metadata_path),
            "trace_receipt_sha256": _sha256(
                report_root / "verification" / "receipt.json"
            ),
            "final_success": source.run_summary.final_success,
        },
    )
    return final_root


# generate_final_report에서 호출된다. trace 단계가 이미 기록한 metadata.json을
# 다시 TraceSource로 재구성해, HTML 렌더링에 동일한 검증된 데이터를 재사용한다.
def _source_from_metadata(metadata: JsonObject) -> TraceSource:
    source_payload: JsonObject = {
        "schema": TRACE_SOURCE_SCHEMA,
        "run_summary": _object(metadata, "run_summary"),
        "no_fake_claim_audit": _object(metadata, "no_fake_claim_audit"),
        "images": _list(metadata, "images"),
        "candidates": _list(metadata, "candidates"),
        "relations": _list(metadata, "relations"),
        "budget": _object(metadata, "budget"),
        "scale_metadata": _object(metadata, "scale_metadata"),
        "tile_metadata": _object(metadata, "tile_metadata"),
    }
    return parse_trace_source(source_payload)


# _source_from_metadata에서 metadata의 중첩 객체 필드를 꺼낼 때 쓰는 헬퍼.
def _object(payload: JsonObject, field: str) -> JsonObject:
    value = payload.get(field)
    if not isinstance(value, dict):
        raise ContractValidationError(field, "trace metadata object is missing")
    return value


def _list(payload: JsonObject, field: str) -> list[JsonValue]:
    value = payload.get(field)
    if not isinstance(value, list):
        raise ContractValidationError(field, "trace metadata list is missing")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
