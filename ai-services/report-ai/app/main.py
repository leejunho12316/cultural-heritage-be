"""보고서 양식 참고 + 생성 AI 서비스.

Spring(ASSESSMENT_RUN 오케스트레이션)에서 HTTP로 호출한다.
이 서비스 자체는 GUIDE_TASK/XRAY_JOB/INSPECTION_RESULT_POTTERY를 직접 조회하지
않는다 — 호출자가 각 파트 API를 모아서 넘겨주면, 고정된 도자기 처리보고서
양식(8개 섹션)에 맞춰 report_json을 조립해 돌려준다.

실행:
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .graph import build_graph
from .rag import CHUNKS_PATH, retrieve_reference_context
from .state import State

app = FastAPI(title="Report AI 서비스")
graph = build_graph()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "indexReady": CHUNKS_PATH.is_file(),
    }


class SearchRequest(BaseModel):
    query: str
    filters: dict[str, Any]
    top_k: int = 5


@app.post("/search")
def search(req: SearchRequest):
    try:
        results = retrieve_reference_context(
            req.query, filters=req.filters, top_k=req.top_k
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"results": results}


class GenerateReportRequest(BaseModel):
    artifact_id: str
    # 각 파트 API를 호출해 모은 원본 데이터. 호출자(Spring)가 채워서 보낸다.
    relic_info: dict[str, Any] = Field(default_factory=dict)
    guide_result: dict[str, Any] = Field(default_factory=dict)  # GUIDE_TASK.result
    xray_report_text: str | None = None  # XRAY_JOB.report_text
    xray_regions: list[dict[str, Any]] = Field(default_factory=list)  # XRAY_REGION 행
    pottery_inspection: dict[str, Any] | None = None  # INSPECTION_RESULT_POTTERY


@app.post("/reports/generate")
def generate_report(req: GenerateReportRequest):
    initial_state: State = {
        "artifact_id": req.artifact_id,
        "relic_info": req.relic_info,
        "guide_result": req.guide_result,
        "xray_report_text": req.xray_report_text,
        "xray_regions": req.xray_regions,
        "pottery_inspection": req.pottery_inspection,
        "sections": {},
    }
    result = graph.invoke(initial_state)
    # ASSESSMENT_REPORT.report_json에 그대로 저장할 수 있는 형태.
    return {"report_json": result["report_json"]}
