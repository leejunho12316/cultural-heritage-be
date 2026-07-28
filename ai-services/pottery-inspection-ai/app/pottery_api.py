"""
pottery_api.py

육안조사 AI 모듈을 BE가 HTTP로 호출할 수 있도록 감싼 FastAPI 서버.


이미 있는 analyze_pottery() 파이프라인을 HTTP 엔드포인트 하나로
감싼다.

실행 방법 (별도 설치 필요):
    pip install fastapi uvicorn "python-multipart"
    uvicorn pottery_api:app --host 0.0.0.0 --port 8001

API 사용법/응답 필드 설명은 ../docs/API_SPEC.md 참고.
"""

from __future__ import annotations

import os
import tempfile
import traceback

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from pottery_analyzer import (
    analyze_pottery,
    build_inspection_text,
    build_report_sentence,
    needs_human_review,
)

# BE가 DB에 "이 결과가 어떤 모듈 버전으로 만들어졌는지" 같이 저장해두면
# 나중에 프롬프트/로직이 바뀌었을 때 이전 결과와 구분하기 쉽다. 코드
# 배포 태그가 따로 없으므로 일단 이 파일 자체의 버전 문자열로 대신한다.
MODULE_VERSION = "pottery-inspection-v11"
# 유물 사진은 보통 몇 MB 수준이라 20MB면 넉넉하다. 이 검사 없이는 아주 큰
# 파일이 들어왔을 때 임시 디스크/메모리를 불필요하게 잡아먹을 수 있다.
MAX_UPLOAD_SIZE_BYTES = 20 * 1_000_000

app = FastAPI(
    title="유물 육안조사 AI 모듈",
    description="사진 한 장을 받아 형태/광택/시대/문양/문양별 상태 이상 후보를 분석하고 '육안 Text'를 생성한다.",
    version=MODULE_VERSION,
)

# FE가 이 서비스를 브라우저에서 직접 호출할 가능성을 대비해 CORS를 열어둔다.
# BE가 자기 서버를 통해서만 중계 호출한다면(브라우저가 이 API를 직접 안 부름)
# 이 미들웨어는 없어도 되지만, 있다고 해서 그 경우에 문제가 생기지도 않는다.
# 실제 배포 시에는 allow_origins를 프론트 도메인으로 좁히는 걸 권장한다
# (지금은 팀 내부 데모용이라 전체 허용으로 둔다).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    """BE 헬스체크용. 모델 로딩 여부까지는 확인하지 않는다(빠른 응답이 목적)."""
    return {"status": "ok", "module_version": MODULE_VERSION}


@app.post("/inspect")
async def inspect(
    image: UploadFile = File(..., description="유물 사진 파일(jpg/png 등)"),
    n_calls: int = 3,
    use_vlm_pattern: bool = True,
) -> JSONResponse:
    """유물 사진 한 장을 분석해 육안조사 결과를 반환한다.

    n_calls: 문양 분석 VLM을 몇 번 반복 호출해 합의를 볼지(기본 3). 1로
    주면 합의 검증 없이 단일 결과만 쓴다 - 빠르지만 신뢰도는 떨어진다.
    use_vlm_pattern: False로 주면 문양 분석(VLM 호출) 자체를 건너뛰고
    형태/광택/시대만 반환한다(비용 절감용).

    응답 필드는 ../docs/API_SPEC.md 참고. 소요 시간은 n_calls와 확정된 문양
    수에 따라 달라지며(문양마다 클로즈업 상태조사 호출이 추가로 붙는다),
    사진 한 장에 VLM 호출이 여러 번(최소 n_calls회 + 확정 문양 수)
    나가므로 BE 쪽 타임아웃을 넉넉히(예: 60~120초) 잡는 걸 권장한다.
    """
    suffix = os.path.splitext(image.filename or "")[1] or ".jpg"
    tmp_path: str | None = None
    try:
        contents = await image.read()
        if not contents:
            raise HTTPException(status_code=400, detail="빈 파일입니다.")
        if len(contents) > MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"파일이 너무 큽니다({len(contents) / 1_000_000:.1f}MB). "
                f"최대 {MAX_UPLOAD_SIZE_BYTES / 1_000_000:.0f}MB까지 허용합니다.",
            )

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_file:
            tmp_file.write(contents)
            tmp_path = tmp_file.name

        try:
            result, _output_image, _bundle = analyze_pottery(
                tmp_path,
                use_vlm_pattern=use_vlm_pattern,
                n_calls=n_calls,
            )
        except Exception as error:  # noqa: BLE001 - BE에는 원인만 넘기고 트레이스는 로그로
            traceback.print_exc()
            raise HTTPException(
                status_code=500, detail=f"분석 중 오류가 발생했습니다: {error}"
            ) from error
    finally:
        if tmp_path is not None and os.path.exists(tmp_path):
            os.remove(tmp_path)

    if result.get("status") != "성공":
        # 유물 실루엣을 못 찾은 경우 등 - BE가 "재촬영 요청" 같은 UX로
        # 분기할 수 있도록 4xx로 응답한다(서버 쪽 문제가 아니라 입력
        # 사진 자체의 문제일 가능성이 높으므로 500이 아니라 422).
        raise HTTPException(
            status_code=422,
            detail=result.get("reason", result.get("status", "분석 실패")),
        )

    payload = {
        "module_version": MODULE_VERSION,
        # DB의 "조사 결과 Text"에 그대로 저장하면 되는 자연어 텍스트.
        "inspection_text": build_inspection_text(result),
        # 로그/대시보드용 한 줄 기술 요약(Gradio 데모 화면과 동일한 문자열).
        "summary": build_report_sentence(result),
        # 전문가 검토 대기열에서 우선순위를 매길 때 참고할 신호. AI가 내리는
        # 최종 승인/반려가 아니라 "먼저 봐야 할 가능성이 높다"는 힌트일 뿐이다.
        "human_review_recommended": needs_human_review(result),
        # 문양별 위치/신뢰도/상태 등 전체 상세 JSON. 당장 안 쓰더라도
        # 나중에 필요할 수 있어 함께 반환한다.
        "detail": result,
    }
    return JSONResponse(content=payload)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
