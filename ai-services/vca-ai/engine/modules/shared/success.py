"""Final-success and exit-code policy for pipeline orchestration."""

from dataclasses import dataclass

from modules.shared.errors import ContractValidationError
from modules.shared.models import ExitCode, RunStatus

_REPORT_GENERATING_STAGE = "report_generating"


@dataclass(frozen=True, slots=True)
class FinalSuccessEvaluation:
    """Value-level final status, success flag, and process exit code."""

    status: RunStatus
    final_success: bool
    exit_code: ExitCode


@dataclass(frozen=True, slots=True)
class FinalSuccessInput:
    """최종 파이프라인 성공 여부를 판단하는 데 필요한 공유 입력 전체.

    모든 필드는 orchestrator가 이미 갖고 있거나 스테이지 출력 아티팩트에서
    읽을 수 있는 데이터에서 나온다 - 아직 존재하지 않는 계측을 필요로 하는
    필드는 여기 없다(각 값이 어떻게 도출되는지는 `orchestration.stage_execution`
    참고).
    """

    dry_run: bool
    failed_stage: str | None
    rough_masking_blocked: bool
    accepted_candidate_count: int


# 파이프라인 전체를 RunStatus 6종 중 하나로 판정한다. 실행 순서대로 확인:
# dry-run -> rough_masking 예산 게이트 차단 -> report_generating만 실패(자체
# 검증 실패, 하지만 후보 데이터는 있음) -> 그 외 스테이지 실패 -> 전 스테이지
# 완료했지만 최종 채택 후보 0개 -> 성공.
def evaluate_final_success(value: FinalSuccessInput) -> FinalSuccessEvaluation:
    """Evaluate the whole-run status from stage outcomes and final counts."""
    if value.accepted_candidate_count < 0:
        field = "accepted_candidate_count"
        raise ContractValidationError(field, "must be non-negative")
    if value.dry_run:
        return FinalSuccessEvaluation(
            status=RunStatus.INCOMPLETE_PRE_QWEN_PREVIEW,
            final_success=False,
            exit_code=ExitCode.OK,
        )
    if value.rough_masking_blocked:
        return FinalSuccessEvaluation(
            status=RunStatus.BLOCKED,
            final_success=False,
            exit_code=ExitCode.INCOMPLETE_OR_FAILURE,
        )
    if value.failed_stage == _REPORT_GENERATING_STAGE:
        return FinalSuccessEvaluation(
            status=RunStatus.INCOMPLETE,
            final_success=False,
            exit_code=ExitCode.INCOMPLETE_OR_FAILURE,
        )
    if value.failed_stage is not None:
        return FinalSuccessEvaluation(
            status=RunStatus.FAILURE,
            final_success=False,
            exit_code=ExitCode.INCOMPLETE_OR_FAILURE,
        )
    if value.accepted_candidate_count == 0:
        return FinalSuccessEvaluation(
            status=RunStatus.NO_VALID_ROUGH_TARGETS,
            final_success=False,
            exit_code=ExitCode.INCOMPLETE_OR_FAILURE,
        )
    return FinalSuccessEvaluation(
        status=RunStatus.SUCCESS,
        final_success=True,
        exit_code=ExitCode.OK,
    )
