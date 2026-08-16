"""보고서 양식 참고 + 생성 AI 서비스.

Spring(ASSESSMENT_RUN 오케스트레이션)에서 HTTP로 호출한다.
이 서비스 자체는 GUIDE_TASK/XRAY_JOB/INSPECTION_RESULT_POTTERY를 직접 조회하지
않는다 — 호출자가 각 파트 API를 모아서 넘겨주면, 고정된 도자기 처리보고서
양식(8개 섹션)에 맞춰 report_json을 조립해 돌려준다.

실행:
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

import base64
from io import BytesIO
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .docx_export import render_report_docx
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


class PhotoAttachment(BaseModel):
    caption: str = ""
    # data URI 접두사(data:image/...;base64,) 없이 순수 base64 문자열만 받는다.
    image_base64: str


class GenerateReportRequest(BaseModel):
    artifact_id: str
    # 각 파트 API를 호출해 모은 원본 데이터. 호출자(Spring)가 채워서 보낸다.
    relic_info: dict[str, Any] = Field(default_factory=dict)
    guide_result: dict[str, Any] = Field(default_factory=dict)  # GUIDE_TASK.result
    xray_report_text: str | None = None  # XRAY_JOB.report_text
    xray_regions: list[dict[str, Any]] = Field(default_factory=list)  # XRAY_REGION 행
    pottery_inspection: dict[str, Any] | None = None  # INSPECTION_RESULT_POTTERY
    # docx에 붙일 사진(작업 전/후, X-ray 원본 등). S3_FILE에서 조회한 이미지를
    # 호출자가 base64로 인코딩해서 넘긴다 - report-ai는 파일을 직접 조회하지 않는다.
    # 키는 어느 단계/조사 항목에 붙일지를 나타낸다 - header/
    # pre_investigation_xray/pre_investigation_visual/disassembly/cleaning/
    # reinforcement/bonding/restoration/conclusion. pre_investigation은 X-ray
    # 조사와 육안조사를 한 섹션 안에서 나눠 서술하므로(docx_export.py 참고)
    # 사진도 "pre_investigation"이 아니라 이 두 세부 key로 보내야 각 조사
    # 서술 바로 뒤에 붙는다.
    photos: dict[str, list[PhotoAttachment]] = Field(default_factory=dict)


def _run_pipeline(req: GenerateReportRequest) -> dict[str, Any]:
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
    return result["report_json"]


@app.post("/reports/generate")
def generate_report(req: GenerateReportRequest):
    # ASSESSMENT_REPORT.report_json에 그대로 저장할 수 있는 형태.
    return {"report_json": _run_pipeline(req)}


@app.post("/reports/generate/docx")
def generate_report_docx(req: GenerateReportRequest):
    """report_json 생성 + .docx 변환까지 한 번에 — 다운로드용 파일 바이트를 반환한다.

    데모/직접 테스트용. 실제 운영에서는 그래프를 매번 다시 돌리면
    LLM 호출 비용이 중복 발생하므로, Spring은 /reports/generate로 한 번 만든
    report_json을 ASSESSMENT_REPORT에 저장해두고 /reports/docx로 변환만
    반복 요청하는 흐름을 쓰는 게 맞다.
    """
    report_json = _run_pipeline(req)
    return _docx_response(req.artifact_id, report_json, req.photos)


class DocxRequest(BaseModel):
    artifact_id: str
    report_json: dict[str, Any]  # ASSESSMENT_REPORT.report_json에 저장된 값 그대로
    photos: dict[str, list[PhotoAttachment]] = Field(default_factory=dict)


@app.post("/reports/docx")
def report_to_docx(req: DocxRequest):
    """이미 생성/저장된 report_json을 .docx로 변환만 한다 (LLM 재호출 없음)."""
    return _docx_response(req.artifact_id, req.report_json, req.photos)


def _docx_response(
    artifact_id: str,
    report_json: dict[str, Any],
    photos: dict[str, list[PhotoAttachment]] | None = None,
) -> StreamingResponse:
    decoded_photos = {
        section_key: [
            {"caption": p.caption, "image": base64.b64decode(p.image_base64)}
            for p in section_photos
        ]
        for section_key, section_photos in (photos or {}).items()
    }
    docx_bytes = render_report_docx(report_json, photos=decoded_photos)
    filename = f"report_{artifact_id}.docx"
    return StreamingResponse(
        BytesIO(docx_bytes),
        media_type=(
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document"
        ),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
