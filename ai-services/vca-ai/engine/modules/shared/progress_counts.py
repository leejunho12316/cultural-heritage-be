"""Mid-stage progress-count reporting shared by heavy startup stages.

Lives in modules.shared (not modules.orchestration) because rough_masking
and mask_refining - upstream stages - need to call it from inside their own
per-unit loops, and a stage importing the orchestration coordinator that
calls it would invert the dependency direction.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, TypedDict

from modules.shared.errors import ContractValidationError
from modules.shared.paths import receipt_file_path

if TYPE_CHECKING:
    from pathlib import Path


class StageProgressCount(TypedDict):
    """How many of a stage's input units are done, out of how many total."""

    completed: int
    total: int


# 지금 실행 중인 스테이지 안에서 처리한 입력 단위 개수(오브젝트/후보 등)를
# progress.json에 중간 기록한다. 무거운 스테이지(rough_masking,
# mask_refining)가 자기 루프 안에서 매 반복 후 호출해, FE 프로그레스 바가
# "현재 스테이지 자체의 0~100%"를 그릴 수 있게 한다. orchestration의
# _write_progress가 스테이지 시작 시점에 이미 current_stage를 써둔 상태라고
# 가정하고 그 payload를 읽어 current_stage_progress 필드만 갱신한다 - 단일
# 프로세스·단일 스레드 순차 실행이라 동시 쓰기 경합은 없다. progress.json이
# 아직 없거나 읽을 수 없으면(예: 이 함수가 orchestration 밖 단독 실행에서
# 호출된 경우) 조용히 건너뛴다 - 진행률 표시는 부가 기능이라 실제 분석을
# 막으면 안 된다.
def update_stage_progress_count(output_root: Path, completed: int, total: int) -> None:
    """Record how many of the current stage's input units are done."""
    if completed < 0 or total < 1 or completed > total:
        field = "stage_progress_count"
        reason = (
            f"completed={completed} and total={total} must satisfy "
            "0<=completed<=total and total>=1"
        )
        raise ContractValidationError(field, reason)
    progress_path = receipt_file_path(output_root, "progress.json")
    try:
        payload = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict):
        return
    payload["current_stage_progress"] = {"completed": completed, "total": total}
    temporary = progress_path.with_suffix(f"{progress_path.suffix}.tmp")
    _ = temporary.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    _ = temporary.replace(progress_path)
