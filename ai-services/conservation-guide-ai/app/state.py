from typing import TypedDict, Annotated, Optional, Literal
from datetime import datetime, timedelta
import functools

# reducer1 : results 딕셔너리를 통째로 덮어쓰지 않고 병합하는 코드 (State Annoted에서 사용)
def merge_results(left, right):
    merged = dict(left or {})
    for k, v in (right or {}).items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    return merged

# reducer2 : 재작업 이력처럼 계속 누적돼야 하는 리스트 병합하는 코드 (State Annoted에서 사용)
def append_list(left, right):
    return (left or []) + (right or [])

class State(TypedDict, total=False):
    # ── 작업 식별 (task_id 는 checkpointer 의 thread_id 로도 사용) ──
    task_id: str
    task_name: str
    task_date: str
    task_manager: str

    # ── 유물 기본정보 (거의 모든 노드가 참조하는 공통 컨텍스트) ──
    relic_info: dict
    relic_photo: list

    # ── Flow 설계 (실행 전 사람이 확정) ──
    flow: list
    cur_flow: Optional[str]

    # ── 단계별 결과 (핵심) ──
    results: Annotated[dict, merge_results]

    # ── 재작업 이력 ──
    rework: Annotated[list, append_list]

    # ── 처리 후 기록 ──
    document_raw: Optional[str]
    document_path: dict

    # ── 진행 상태 메타데이터 ──
    total_state: Literal["draft", "in_progress", "paused", "completed"]
    last_edited_date: str

    # ── 처리 전 조사 단계 AI 기술 결과들 ──
    ai_xray_path : str
    ai_visual_inspection : dict
    ai_photo_path : str

class StageResult(TypedDict, total=False):
    status: Literal["pending", "in_progress", "completed", "skipped"]
    photo: list                     # 이 단계에서 촬영/업로드한 이미지 경로
    memo: Optional[str]        # 담당자가 남긴 메모
    state_datetime: Optional[str]
    stage_manager: Optional[str]

# 날짜 뱉는 함수
def _now():
  KST = timedelta(hours=9)
  return (datetime.now() + KST).isoformat(timespec="seconds")

# 노드에서 실행하는 StageResult 조립 함수
def _build_result(photo_urls : list, memo : str, manager : str):
  return {
      "status": "completed",
      "photo": photo_urls,
      'memo' : memo,
      "stage_manager": manager,
      "state_datetime": _now(),
  }

def stage_guard(step_name):
  def deco(func):
      @functools.wraps(func)
      def wrapper(state: State):

          #flow에 없으면 skip
          if step_name not in state.get("flow", []):
              return {
                  "cur_flow": step_name,
                  "results": {step_name: {"status": "skipped"}},
                  "last_edited_date": _now(),
              }

          return func(state)
      return wrapper
  return deco

