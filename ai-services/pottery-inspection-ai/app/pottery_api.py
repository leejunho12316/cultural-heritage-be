"""
pottery_api.py

육안조사 AI 모듈을 BE가 HTTP로 호출할 수 있도록 감싼 FastAPI 서버.


이미 있는 analyze_pottery() 파이프라인을 HTTP 엔드포인트 하나로
감싼다.

실행 방법 (별도 설치 필요):
    pip install fastapi uvicorn "python-multipart"
    uvicorn pottery_api:app --host 0.0.0.0 --port 8000

API 사용법/응답 필드 설명은 ../docs/API_SPEC.md 참고.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
import traceback
import uuid
from dataclasses import dataclass, field

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
    treat_as_single_artifact: bool = False,
) -> JSONResponse:
    """유물 사진 한 장을 분석해 육안조사 결과를 반환한다.

    n_calls: 문양 분석 VLM을 몇 번 반복 호출해 합의를 볼지(기본 3). 1로
    주면 합의 검증 없이 단일 결과만 쓴다 - 빠르지만 신뢰도는 떨어진다.
    use_vlm_pattern: False로 주면 문양 분석(VLM 호출) 자체를 건너뛰고
    형태/광택/시대만 반환한다(비용 절감용).
    treat_as_single_artifact: 사진에서 서로 떨어진 영역이 여러 개 감지돼
    "다중 객체 감지"로 한 번 막혔을 때, 사용자가 "이건 하나의 유물이 깨진
    조각들이다"라고 확인한 뒤 재요청할 때만 True로 보낸다. 기본값(False)이면
    지금까지처럼 다중 객체는 재촬영 안내로 막는다 - 사진만으로 "다른 유물
    여러 개"와 "깨진 조각들"을 구분하는 건 근본적으로 애매해서, 자동 판단
    대신 사람 확인을 받는 쪽을 선택했다.

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
                treat_as_single_artifact=treat_as_single_artifact,
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
        status_code, detail = _error_from_result(result)
        raise HTTPException(status_code=status_code, detail=detail)

    return JSONResponse(content=_build_success_payload(result))


def _error_from_result(result: dict) -> tuple[int, dict | str]:
    """analyze_pottery()가 실패로 돌려준 result를 (상태코드, detail)로 바꾼다.

    동기 엔드포인트(/inspect)의 HTTPException과, 아래 job 방식의 실패 저장이
    똑같은 형태를 쓰도록 공통으로 뺐다.
    """
    if result.get("status") == "다중 객체 감지":
        # FE가 "재촬영하라"는 일반 에러와 "하나의 유물이 깨진 조각인지
        # 확인해달라"는 재시도 가능한 상황을 구분할 수 있도록, detail을
        # 문자열이 아니라 구조화된 값으로 준다.
        return 422, {
            "code": "MULTIPLE_OBJECTS_DETECTED",
            "message": result.get("reason", "다중 객체 감지"),
            "detected_region_count": result.get("detected_region_count"),
            "region_groups": result.get("region_groups"),
        }
    # 유물 실루엣을 못 찾은 경우 등 - BE가 "재촬영 요청" 같은 UX로 분기할 수
    # 있도록 4xx로 응답한다(서버 쪽 문제가 아니라 입력 사진 자체의 문제일
    # 가능성이 높으므로 500이 아니라 422).
    return 422, result.get("reason", result.get("status", "분석 실패"))


def _build_success_payload(result: dict) -> dict:
    return {
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


# ============================================================
# 작업 접수 + 폴링 방식 (/inspect/jobs)
#
# 문양이 여러 개 잡히면 VLM 호출(문양 식별 → 확정된 문양별 상태조사, 이
# 둘은 순서상 이어져 있어 병렬화가 안 됨)이 쌓여 60초를 넘기는 경우가
# 있었다. 동기 방식(/inspect)은 그 시간만큼 ALB가 연결을 붙들고 있어야
# 해서, ALB 유휴 타임아웃(기본 60초)에 걸려 504가 났다. 여기서는 요청을
# 접수만 하고 즉시 응답한 뒤, 실제 결과는 짧은 폴링 요청으로 받는다 -
# 개별 요청은 전부 순식간에 끝나므로 ALB 타임아웃과 무관해진다.
#
# 잡 상태는 이 프로세스 메모리에만 둔다. pottery-inspection-ai가 지금처럼
# 인스턴스 1개일 때만 안전하다 - 나중에 여러 개로 늘리면 폴링 요청이 잡을
# 만든 인스턴스가 아닌 다른 인스턴스로 갈 수 있어 이 방식이 깨진다. 그때는
# Redis 등 인스턴스 간에 공유되는 저장소로 옮겨야 한다.
# ============================================================


@dataclass
class _InspectionJob:
    status: str  # "queued" | "processing" | "done" | "failed"
    result: dict | None = None
    error_status_code: int | None = None
    error_detail: dict | str | None = None
    created_at: float = field(default_factory=time.time)


_JOBS: dict[str, _InspectionJob] = {}
_JOBS_LOCK = asyncio.Lock()
_JOB_RETENTION_SECONDS = 30 * 60  # 30분 지난 잡은 폴링 안 해갔다고 보고 정리한다.


async def _prune_stale_jobs() -> None:
    cutoff = time.time() - _JOB_RETENTION_SECONDS
    async with _JOBS_LOCK:
        stale_ids = [job_id for job_id, job in _JOBS.items() if job.created_at < cutoff]
        for job_id in stale_ids:
            del _JOBS[job_id]


async def _run_inspection_job(
    job_id: str,
    tmp_path: str,
    n_calls: int,
    use_vlm_pattern: bool,
    treat_as_single_artifact: bool,
) -> None:
    async with _JOBS_LOCK:
        _JOBS[job_id].status = "processing"

    try:
        # analyze_pottery 자체는 동기(블로킹) 함수라, 이벤트 루프를 막지
        # 않도록 별도 스레드에서 돌린다. 내부에서 쓰는
        # concurrent.futures.ThreadPoolExecutor(VLM 병렬 호출)와는 별개다.
        result, _output_image, _bundle = await asyncio.to_thread(
            analyze_pottery,
            tmp_path,
            use_vlm_pattern=use_vlm_pattern,
            n_calls=n_calls,
            treat_as_single_artifact=treat_as_single_artifact,
        )
    except Exception as error:  # noqa: BLE001 - BE에는 원인만 넘기고 트레이스는 로그로
        traceback.print_exc()
        async with _JOBS_LOCK:
            _JOBS[job_id].status = "failed"
            _JOBS[job_id].error_status_code = 500
            _JOBS[job_id].error_detail = f"분석 중 오류가 발생했습니다: {error}"
        return
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    async with _JOBS_LOCK:
        if result.get("status") != "성공":
            status_code, detail = _error_from_result(result)
            _JOBS[job_id].status = "failed"
            _JOBS[job_id].error_status_code = status_code
            _JOBS[job_id].error_detail = detail
        else:
            _JOBS[job_id].status = "done"
            _JOBS[job_id].result = _build_success_payload(result)


@app.post("/inspect/jobs")
async def create_inspection_job(
    image: UploadFile = File(..., description="유물 사진 파일(jpg/png 등)"),
    n_calls: int = 3,
    use_vlm_pattern: bool = True,
    treat_as_single_artifact: bool = False,
) -> JSONResponse:
    """분석을 백그라운드로 접수하고 즉시 job_id를 반환한다.

    실제 결과는 GET /inspect/jobs/{job_id}로 폴링해서 받는다. 파라미터
    의미는 POST /inspect와 동일하다.
    """
    await _prune_stale_jobs()

    suffix = os.path.splitext(image.filename or "")[1] or ".jpg"
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

    job_id = str(uuid.uuid4())
    async with _JOBS_LOCK:
        _JOBS[job_id] = _InspectionJob(status="queued")

    asyncio.create_task(
        _run_inspection_job(job_id, tmp_path, n_calls, use_vlm_pattern, treat_as_single_artifact)
    )

    return JSONResponse(status_code=202, content={"job_id": job_id, "status": "queued"})


@app.get("/inspect/jobs/{job_id}")
async def get_inspection_job(job_id: str) -> JSONResponse:
    """job 상태를 폴링한다. FE는 보통 1~2초 간격으로 이걸 반복 호출한다."""
    async with _JOBS_LOCK:
        job = _JOBS.get(job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="해당 job_id를 찾을 수 없습니다(만료됐거나 잘못된 id).",
        )

    if job.status in ("queued", "processing"):
        return JSONResponse(content={"status": job.status})

    if job.status == "failed":
        # /inspect가 실패했을 때와 동일한 형태(status_code + detail)로 실어
        # 보낸다 - FE의 기존 에러 처리 로직을 그대로 재사용하기 위함.
        return JSONResponse(
            status_code=job.error_status_code or 500,
            content={"status": "failed", "detail": job.error_detail},
        )

    return JSONResponse(content={"status": "done", "result": job.result})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
