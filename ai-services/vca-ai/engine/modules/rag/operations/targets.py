"""Immutable RAG follow-up target aggregation."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NoReturn

from modules.shared import (
    CandidateId,
    ContractValidationError,
    FollowupMode,
    FollowupSelectorType,
    UserFollowupRequest,
)


@dataclass(frozen=True, slots=True)
class CoverageMetric:
    """One named target-coverage measurement."""

    name: str
    value: float

    def __post_init__(self) -> None:
        """Reject metrics that cannot be reported deterministically."""
        if not self.name.strip():
            _raise_contract("coverage_metric.name", "must not be blank")
        if not math.isfinite(self.value) or self.value < 0.0:
            _raise_contract(
                "coverage_metric.value",
                "must be finite and non-negative",
            )


@dataclass(frozen=True, slots=True)
class SelectedParentTarget:
    """Resolved parent selector retained for target accounting."""

    selector_type: FollowupSelectorType
    selector_id: str
    valid: bool

    def __post_init__(self) -> None:
        """Reject parent selectors without a stable identifier."""
        if not self.selector_id.strip():
            _raise_contract(
                "selected_parent.selector_id",
                "must not be blank",
            )


@dataclass(frozen=True, slots=True)
class TargetResolution:
    """Resolved target details shared by both follow-up origins."""

    candidate_id: CandidateId
    selected_parent: SelectedParentTarget
    rag_parent_candidate_id: CandidateId | None
    same_anomaly_group_id: str | None
    source_candidate_ids: tuple[CandidateId, ...]
    coverage_metrics: tuple[CoverageMetric, ...]
    duplicate_suppression_key: str
    skip_reason: str | None = None

    def __post_init__(self) -> None:
        """Reject incomplete resolution metadata before aggregation."""
        if not self.candidate_id.strip():
            _raise_contract("candidate_id", "must not be blank")
        if not self.source_candidate_ids:
            _raise_contract(
                "source_candidate_ids",
                "must not be empty",
            )
        if not self.duplicate_suppression_key.strip():
            _raise_contract(
                "duplicate_suppression_key",
                "must not be blank",
            )
        if self.skip_reason is not None and not self.skip_reason.strip():
            _raise_contract("skip_reason", "must not be blank")


@dataclass(frozen=True, slots=True)
class _TargetOrigin:
    followup_mode: FollowupMode
    followup_reason: str
    trigger_priority: int


@dataclass(frozen=True, slots=True)
class RagTarget:
    """Common automatic and user-requested RAG target schema."""

    candidate_id: CandidateId
    followup_mode: FollowupMode
    followup_reason: str
    trigger_priority: int
    selected_parent: SelectedParentTarget
    rag_parent_candidate_id: CandidateId | None
    same_anomaly_group_id: str | None
    source_candidate_ids: tuple[CandidateId, ...]
    coverage_metrics: tuple[CoverageMetric, ...]
    duplicate_suppression_key: str
    skip_reason: str | None


# orchestration이 자동 트리거(예: 예산/커버리지 규칙)로 결정한 후속 target을
# 만든다. user_requested_target과 함께 aggregate_targets로 합쳐진다.
def automatic_target(
    resolution: TargetResolution,
    followup_reason: str,
    trigger_priority: int,
) -> RagTarget:
    """Create one automatic target from resolved parent metadata."""
    _validate_reason_priority(followup_reason, trigger_priority)
    return _target(
        resolution,
        _TargetOrigin(FollowupMode.AUTOMATIC, followup_reason, trigger_priority),
    )


# 사용자가 명시적으로 요청한 후속 target을 만든다. 요청의 selector가 이미
# resolution.selected_parent와 일치한다고 가정하지 않고 여기서 다시 대조한다.
def user_requested_target(
    request: UserFollowupRequest,
    resolution: TargetResolution,
) -> RagTarget:
    """Create one target from a validated shared follow-up request."""
    if (
        request.selector_type is not resolution.selected_parent.selector_type
        or request.selector_id != resolution.selected_parent.selector_id
    ):
        _raise_contract(
            "selected_parent",
            "must match the validated user request selector",
        )
    return _target(
        resolution,
        _TargetOrigin(
            FollowupMode.USER_REQUESTED,
            request.followup_reason,
            request.trigger_priority,
        ),
    )


# 자동/사용자 요청 두 target 목록을 각자의 followup_mode가 맞는지 검증한 뒤
# 순서를 바꾸지 않고 이어붙인다. orchestration이 실행할 전체 target 집합을
# 확정할 때 호출한다.
def aggregate_targets(
    automatic_targets: Sequence[RagTarget],
    user_requested_targets: Sequence[RagTarget],
) -> tuple[RagTarget, ...]:
    """Combine both target origins without changing stable input order."""
    if any(
        target.followup_mode is not FollowupMode.AUTOMATIC
        for target in automatic_targets
    ):
        _raise_contract(
            "automatic_targets.followup_mode",
            "must be automatic",
        )
    if any(
        target.followup_mode is not FollowupMode.USER_REQUESTED
        for target in user_requested_targets
    ):
        _raise_contract(
            "user_requested_targets.followup_mode",
            "must be user_requested",
        )
    return (*automatic_targets, *user_requested_targets)


def _target(
    resolution: TargetResolution,
    origin: _TargetOrigin,
) -> RagTarget:
    return RagTarget(
        candidate_id=resolution.candidate_id,
        followup_mode=origin.followup_mode,
        followup_reason=origin.followup_reason,
        trigger_priority=origin.trigger_priority,
        selected_parent=resolution.selected_parent,
        rag_parent_candidate_id=resolution.rag_parent_candidate_id,
        same_anomaly_group_id=resolution.same_anomaly_group_id,
        source_candidate_ids=resolution.source_candidate_ids,
        coverage_metrics=resolution.coverage_metrics,
        duplicate_suppression_key=resolution.duplicate_suppression_key,
        skip_reason=resolution.skip_reason,
    )


def _validate_reason_priority(followup_reason: str, trigger_priority: int) -> None:
    if not followup_reason.strip():
        _raise_contract("followup_reason", "must not be blank")
    if trigger_priority < 0:
        _raise_contract("trigger_priority", "must be non-negative")


def _raise_contract(field: str, reason: str) -> NoReturn:
    raise ContractValidationError(field, reason)
