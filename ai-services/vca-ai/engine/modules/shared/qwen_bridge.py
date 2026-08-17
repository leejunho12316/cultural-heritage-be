"""Shared Qwen bridge handoff contracts."""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, NoReturn

from modules.shared.bridge_query import QwenRagQueryInput
from modules.shared.errors import ContractValidationError
from modules.shared.models import CandidateId

BRIDGE_SCHEMA_VERSION: Final = "qwen-rag-bridge-v1"
_QWEN_ANOMALY_CLASS_TOKENS: Final = frozenset(
    {
        "accretion",
        "adhesive",
        "biological",
        "corrosion",
        "crack",
        "deformation",
        "deposit",
        "discoloration",
        "fissure",
        "flaking",
        "growth",
        "hole",
        "loss",
        "pit",
        "pitting",
        "residue",
        "spalled",
        "spalling",
        "stain",
    }
)
_MORPHOLOGY_DESCRIPTOR_TOKENS: Final = frozenset({"flaking", "hole", "pit"})
# Qwen이 불확실할 때 실제로 내보내는 무의미한 자리표시자 값들 - 검색 쿼리에
# 그대로 섞여 들어가면 벡터 검색을 흐리기만 한다(실측: 'unknown'이
# extracted_descriptors에 그대로 나온 사례 확인됨). anomaly-class 차단
# 목록과는 다른 이유(진단이 아니라 "값 없음")로 항상 걸러진다.
_NON_DESCRIPTIVE_PLACEHOLDER_TOKENS: Final = frozenset(
    {"unknown", "none", "n/a", "na", "unclear", "unspecified", "unidentified"}
)


class QwenBridgeStatus(StrEnum):
    """Non-null Qwen bridge result states consumed by RAG accounting."""

    SUCCESS = "success"
    FAILED = "failed"


class BridgeFieldUsage(StrEnum):
    """Allowed downstream usage classes for Qwen bridge fields."""

    QUERY_DRIVING = "query_driving"
    PROVENANCE_ONLY = "provenance_only"


class BridgeFieldRole(StrEnum):
    """Qwen bridge fields with locked C-004 usage semantics."""

    SELECTED_TERMS = "selected_terms"
    EXTRACTED_DESCRIPTORS = "extracted_descriptors"
    CONFIDENCE = "confidence"
    REASON = "reason"
    QWEN_OBSERVATION_ID = "qwen_observation_id"
    INPUT_VIEW_HASHES = "input_view_hashes"


FIELD_ROLE_USAGE: Final[Mapping[BridgeFieldRole, BridgeFieldUsage]] = MappingProxyType(
    {
        BridgeFieldRole.SELECTED_TERMS: BridgeFieldUsage.QUERY_DRIVING,
        BridgeFieldRole.EXTRACTED_DESCRIPTORS: BridgeFieldUsage.QUERY_DRIVING,
        BridgeFieldRole.CONFIDENCE: BridgeFieldUsage.PROVENANCE_ONLY,
        BridgeFieldRole.REASON: BridgeFieldUsage.PROVENANCE_ONLY,
        BridgeFieldRole.QWEN_OBSERVATION_ID: BridgeFieldUsage.PROVENANCE_ONLY,
        BridgeFieldRole.INPUT_VIEW_HASHES: BridgeFieldUsage.PROVENANCE_ONLY,
    }
)


def field_usage(role: BridgeFieldRole) -> BridgeFieldUsage:
    """Return the only allowed downstream usage for a Qwen bridge field."""
    return FIELD_ROLE_USAGE[role]


@dataclass(frozen=True, slots=True)
class QwenBridgeResult:
    """Shared non-null Qwen handoff record consumed by RAG."""

    candidate_id: CandidateId
    status: QwenBridgeStatus
    selected_terms: tuple[str, ...]
    extracted_descriptors: tuple[str, ...]
    confidence: float | None
    reason: str
    qwen_observation_id: str | None
    input_view_hashes: tuple[str, ...]
    failure_code: str | None = None

    def query_driving_fields(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return the only Qwen-derived fields allowed to drive RAG queries."""
        return self.selected_terms, self.extracted_descriptors

    def to_rag_query_input(self) -> QwenRagQueryInput:
        """Return the provenance-free RAG query handoff."""
        return QwenRagQueryInput(
            self.candidate_id,
            self.selected_terms,
            self.extracted_descriptors,
        )

    def __post_init__(self) -> None:
        """Reject Qwen bridge states that would make RAG accounting ambiguous."""
        object.__setattr__(
            self,
            "selected_terms",
            _sanitize_qwen_query_terms(self.selected_terms),
        )
        object.__setattr__(
            self,
            "extracted_descriptors",
            _sanitize_qwen_query_terms(
                self.extracted_descriptors,
                preserve_morphology_tokens=True,
            ),
        )
        _validate_confidence(self.confidence)
        if not self.reason.strip():
            _raise_contract("reason", "must not be blank")
        match self.status:
            case QwenBridgeStatus.SUCCESS:
                _validate_qwen_success(self)
            case QwenBridgeStatus.FAILED:
                _validate_qwen_failure(self)


def _validate_confidence(confidence: float | None) -> None:
    if confidence is not None and (
        not math.isfinite(confidence) or confidence < 0.0 or confidence > 1.0
    ):
        _raise_contract("confidence", "must be between zero and one")


def _sanitize_qwen_query_terms(
    terms: tuple[str, ...], *, preserve_morphology_tokens: bool = False
) -> tuple[str, ...]:
    blocked_tokens = _QWEN_ANOMALY_CLASS_TOKENS | _NON_DESCRIPTIVE_PLACEHOLDER_TOKENS
    if preserve_morphology_tokens:
        blocked_tokens = blocked_tokens - _MORPHOLOGY_DESCRIPTOR_TOKENS
    sanitized: list[str] = []
    for term in terms:
        tokens = tuple(
            token.strip(".,:;()[]{}").casefold()
            for token in term.replace("_", " ").split()
        )
        value = " ".join(token for token in tokens if token not in blocked_tokens)
        if value:
            sanitized.append(value)
    return tuple(dict.fromkeys(sanitized))


def _validate_qwen_success(result: QwenBridgeResult) -> None:
    if result.qwen_observation_id is None or not result.qwen_observation_id.strip():
        _raise_contract("qwen_observation_id", "success requires observation id")
    if result.failure_code is not None:
        _raise_contract("failure_code", "success cannot include failure code")
    if not result.selected_terms and not result.extracted_descriptors:
        _raise_contract("qwen_query_fields", "success requires query evidence")


def _validate_qwen_failure(result: QwenBridgeResult) -> None:
    if result.failure_code is None or not result.failure_code.strip():
        _raise_contract("failure_code", "failed qwen result requires failure code")
    if result.selected_terms or result.extracted_descriptors:
        _raise_contract("qwen_query_fields", "failed qwen result has no query fields")


def _raise_contract(field: str, reason: str) -> NoReturn:
    raise ContractValidationError(field, reason)
