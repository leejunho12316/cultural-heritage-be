"""Typed directory artifact storage for shared Qwen bridge results."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, NoReturn

from modules.rag.qwen.qwen_bridge_json import JsonObject, parse_json_object
from modules.shared import (
    BRIDGE_SCHEMA_VERSION,
    CandidateId,
    ContractValidationError,
    QwenBridgeResult,
    QwenBridgeStatus,
    ensure_no_symlink_leaf,
    ensure_source_document_is_not_write_target,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

QWEN_BRIDGE_RESULTS_ARTIFACT: Final = "qwen_bridge_results"
QWEN_BRIDGE_RESULT_FILE: Final = "qwen_bridge_result.jsonl"
_BRIDGE_FIELDS: Final = frozenset(
    {
        "candidate_id",
        "confidence",
        "extracted_descriptors",
        "failure_code",
        "input_view_hashes",
        "qwen_observation_id",
        "reason",
        "schema",
        "selected_terms",
        "status",
    }
)


@dataclass(frozen=True, slots=True)
class QwenBridgeCandidateArtifact:
    """One rough candidate's bridge result and source record location."""

    rough_record_path: str
    rough_record_index: int
    result: QwenBridgeResult


def qwen_bridge_results_path(rag_run_directory: Path) -> Path:
    """Return the run-local Qwen bridge artifact directory for a RAG run."""
    return rag_run_directory / QWEN_BRIDGE_RESULTS_ARTIFACT


# Qwen 추론 결과를 rough_record_path/record 인덱스 구조를 그대로 반영하는
# 디렉터리 트리에 후보별 파일로 기록한다. candidate_sidecar_models가 나중에
# qwen_bridge_results_path를 통해 이 디렉터리를 읽는다. 전체를 임시 디렉터리에
# 먼저 쓴 뒤 통째로 교체해 원자성을 보장한다.
def write_qwen_bridge_results(
    rag_run_directory: Path,
    rows: tuple[QwenBridgeCandidateArtifact, ...],
    source_document_root: Path | None = None,
) -> Path:
    """Write validated bridge rows to the run-local Qwen result directory."""
    _validate_unique_candidates(rows)
    artifact_path = qwen_bridge_results_path(rag_run_directory)
    if source_document_root is not None:
        artifact_path = ensure_source_document_is_not_write_target(
            source_document_root, artifact_path
        )
    _validate_unique_candidate_paths(artifact_path, rows)
    temporary_artifact_path = artifact_path.with_name(f".{artifact_path.name}.tmp")
    _ = ensure_no_symlink_leaf(artifact_path, "qwen bridge artifact leaf is a symlink")
    _ = ensure_no_symlink_leaf(
        temporary_artifact_path, "qwen bridge artifact leaf is a symlink"
    )
    if temporary_artifact_path.exists():
        shutil.rmtree(temporary_artifact_path)
    temporary_artifact_path.mkdir(parents=True, exist_ok=True)
    for row in rows:
        candidate_path = _candidate_file_path(temporary_artifact_path, row)
        _write_text_atomic(
            candidate_path,
            json.dumps(_payload(row.result), sort_keys=True, separators=(",", ":"))
            + "\n",
        )
    if artifact_path.exists():
        shutil.rmtree(artifact_path)
    _ = temporary_artifact_path.replace(artifact_path)
    return artifact_path


# write_qwen_bridge_results가 만든 디렉터리를 candidate_id로 색인된 맵으로
# 되돌린다. 예전 단일 JSONL 파일 형식(path가 디렉터리가 아니라 파일인 경우)도
# _read_legacy_jsonl_results로 지원한다.
# CandidateRagSidecarInputs.resolved_qwen_results가 호출한다.
def read_qwen_bridge_results(path: Path) -> Mapping[CandidateId, QwenBridgeResult]:
    """Parse a run-local Qwen result directory into candidate-keyed rows."""
    if path.is_file():
        return _read_legacy_jsonl_results(path)
    if not path.is_dir():
        _raise_contract("qwen_bridge_results_path", "must reference a directory")
    results: dict[CandidateId, QwenBridgeResult] = {}
    for artifact_file in sorted(path.glob(f"**/{QWEN_BRIDGE_RESULT_FILE}")):
        lines = artifact_file.read_text(encoding="utf-8").splitlines()
        if len(lines) != 1:
            _raise_contract(
                "qwen_bridge_result.jsonl",
                "must contain exactly one JSONL row",
            )
        result = _parse_row(lines[0], artifact_file)
        if result.candidate_id in results:
            _raise_contract("candidate_id", "duplicate row in qwen bridge results")
        results[result.candidate_id] = result
    return MappingProxyType(results)


# 디렉터리 트리 형식 이전에 쓰던 단일 JSONL 파일을 읽기 위한 하위 호환 경로.
def _read_legacy_jsonl_results(path: Path) -> Mapping[CandidateId, QwenBridgeResult]:
    results: dict[CandidateId, QwenBridgeResult] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        result = _parse_row(line, path)
        if result.candidate_id in results:
            _raise_contract("candidate_id", "duplicate row in qwen bridge results")
        results[result.candidate_id] = result
    return MappingProxyType(results)


def _validate_unique_candidates(rows: tuple[QwenBridgeCandidateArtifact, ...]) -> None:
    candidate_ids = tuple(row.result.candidate_id for row in rows)
    if len(candidate_ids) != len(set(candidate_ids)):
        _raise_contract("candidate_id", "must be unique per Qwen bridge artifact")


def _validate_unique_candidate_paths(
    artifact_path: Path, rows: tuple[QwenBridgeCandidateArtifact, ...]
) -> None:
    candidate_paths = tuple(_candidate_file_path(artifact_path, row) for row in rows)
    if len(candidate_paths) != len(set(candidate_paths)):
        _raise_contract("qwen_bridge_result", "must have unique output paths")


# rough_record_path의 디렉터리 구조와 record 인덱스를 그대로 반영해 후보별
# 결과 파일 경로를 만든다. write/read 양쪽이 동일한 규칙을 써야 서로 찾을 수
# 있다.
def _candidate_file_path(artifact_path: Path, row: QwenBridgeCandidateArtifact) -> Path:
    record_path = _safe_relative_path(row.rough_record_path)
    if row.rough_record_index < 0:
        _raise_contract("rough_record_index", "must be non-negative")
    return (
        artifact_path
        / record_path.parent
        / f"record-{row.rough_record_index:04d}"
        / QWEN_BRIDGE_RESULT_FILE
    )


def _payload(row: QwenBridgeResult) -> JsonObject:
    return {
        "candidate_id": row.candidate_id,
        "confidence": row.confidence,
        "extracted_descriptors": list(row.extracted_descriptors),
        "failure_code": row.failure_code,
        "input_view_hashes": list(row.input_view_hashes),
        "qwen_observation_id": row.qwen_observation_id,
        "reason": row.reason,
        "schema": BRIDGE_SCHEMA_VERSION,
        "selected_terms": list(row.selected_terms),
        "status": row.status.value,
    }


def _safe_relative_path(path: str) -> Path:
    if not path.strip():
        _raise_contract("rough_record_path", "must be a non-blank relative path")
    relative_path = Path(path)
    if relative_path.is_absolute() or any(part == ".." for part in relative_path.parts):
        _raise_contract("rough_record_path", "must be a safe relative path")
    return relative_path


# JSONL 한 줄을 검증된 QwenBridgeResult로 파싱한다. 필드 집합/스키마 버전이
# 어긋나면 예외를 던진다(다른 리더 함수들처럼 조용히 건너뛰지 않는다).
def _parse_row(line: str, artifact_file: Path) -> QwenBridgeResult:
    payload = _json_object(line, artifact_file)
    _validate_fields(payload)
    _require_schema(payload)
    status = _status(payload)
    return QwenBridgeResult(
        candidate_id=CandidateId(_required_string(payload, "candidate_id")),
        status=status,
        selected_terms=_string_tuple(payload, "selected_terms"),
        extracted_descriptors=_string_tuple(payload, "extracted_descriptors"),
        confidence=_optional_confidence(payload),
        reason=_required_string(payload, "reason"),
        qwen_observation_id=_optional_string(payload, "qwen_observation_id"),
        input_view_hashes=_string_tuple(payload, "input_view_hashes"),
        failure_code=_optional_string(payload, "failure_code"),
    )


def _json_object(line: str, artifact_file: Path) -> JsonObject:
    try:
        return parse_json_object(line)
    except ContractValidationError as error:
        _raise_contract(
            "qwen_bridge_results",
            f"invalid JSON in {artifact_file.name}: {error.reason}",
        )


def _validate_fields(payload: JsonObject) -> None:
    fields = frozenset(payload)
    if fields != _BRIDGE_FIELDS:
        _raise_contract("qwen_bridge_results", "row fields must match bridge schema")


def _require_schema(payload: JsonObject) -> None:
    if _required_string(payload, "schema") != BRIDGE_SCHEMA_VERSION:
        _raise_contract("schema", f"must be {BRIDGE_SCHEMA_VERSION}")


def _status(payload: JsonObject) -> QwenBridgeStatus:
    try:
        return QwenBridgeStatus(_required_string(payload, "status"))
    except ValueError:
        _raise_contract("status", "must be a known Qwen bridge status")


def _required_string(payload: JsonObject, field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        _raise_contract(field, "must be a non-blank string")
    return value


def _optional_string(payload: JsonObject, field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        _raise_contract(field, "must be a string or null")
    return value


def _string_tuple(payload: JsonObject, field: str) -> tuple[str, ...]:
    value = payload.get(field)
    if not isinstance(value, list):
        _raise_contract(field, "must be an array of strings")
    strings: list[str] = []
    for item in value:
        if not isinstance(item, str):
            _raise_contract(field, "must be an array of strings")
        strings.append(item)
    return tuple(strings)


def _optional_confidence(payload: JsonObject) -> float | None:
    value = payload.get("confidence")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        _raise_contract("confidence", "must be a number or null")
    return float(value)


def _raise_contract(field: str, reason: str) -> NoReturn:
    raise ContractValidationError(field, reason)


def _write_text_atomic(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    _ = ensure_no_symlink_leaf(path, "qwen bridge result leaf is a symlink")
    _ = ensure_no_symlink_leaf(temporary_path, "qwen bridge result leaf is a symlink")
    try:
        _ = temporary_path.write_text(payload, encoding="utf-8")
        _ = temporary_path.replace(path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise
