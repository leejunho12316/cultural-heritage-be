from fastapi import FastAPI
from typing import Optional, Literal
from pydantic import BaseModel
from langgraph.types import Command

from .graph import build_graph
from .state import State
from .state import _now

app = FastAPI()
graph = build_graph()

# interrupt시 : 노드 실행이 일시정지되고 반환된 값을 표현.
# 종료시 : 그냥 결과 전체 표현.
def _format_response(result: dict) -> dict:
    if "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        return {"status": "waiting_for_input", "interrupt": payload}
    return {"status": "completed", "result": result}


#tasks/{}/start를 실행할 때 Body로 받아야 하는 것 BaseModel로 스키마 정의
class StartTaskRequest(BaseModel):
    # task 기본 정보
    task_name: str
    task_manager: str

    # relic 정보
    relic_info: dict
    relic_photo: list

    # flow / 상태 정보
    flow: list[str]

@app.post("/tasks/{task_id}/start")
def start_task(task_id: str, req: StartTaskRequest):
    initial_state: State = {
        "task_id": task_id,  # path에서 받은 값 그대로 사용 (기존 'temp'는 오타/임시값이었던 걸로 보여요)
        "task_name": req.task_name,
        "task_date": _now(),
        "task_manager": req.task_manager,

        "relic_info": req.relic_info,
        "relic_photo": req.relic_photo,
        "flow": req.flow,

        "total_state": "in_progress",
        "last_edited_date": _now(),

        "results": {},
    }
    config = {"configurable": {"thread_id": task_id}}
    result = graph.invoke(initial_state, config=config)
    return _format_response(result)



class ResumeTaskRequest(BaseModel):
    resume: dict

@app.post("/tasks/{task_id}/resume")
def resume_task(task_id: str, req: ResumeTaskRequest):

    config = {"configurable": {"thread_id": task_id}}
    result = graph.invoke(Command(resume=req.resume), config=config)

    return _format_response(result)