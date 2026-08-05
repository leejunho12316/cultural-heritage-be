"""
X-ray 이상영역 탐지 라우터

담당: 결함 탐지 파트

이 파일만 수정하면 되므로 다른 파트와 git 충돌이 나지 않는다.
새 엔드포인트가 필요하면 여기에 추가한다.

경로에 접두사를 붙이려면 main.py의 include_router에서
prefix="/anomaly"를 지정한다. 이 파일은 건드리지 않아도 된다.
"""

import json

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.responses import JSONResponse

from app import config
from app.services import detector, reporter
from app.utils import cleanup, save_upload


router = APIRouter(tags=["anomaly"])


# ------------------------------------------------------------
# 단일 이미지 탐지
# ------------------------------------------------------------

@router.post("/detect")
async def detect(
    file: UploadFile = File(...),
    analysis_target: str = Form("원본 조각"),
    confidence: float = Form(None),
    imgsz: int = Form(None),
):
    """
    X-ray 이미지 한 장에서 이상영역을 탐지한다.

    analysis_target
        "결합 완료본" 또는 "원본 조각"

        결합본은 원본이 5000px 이상으로 크기 때문에
        imgsz 2048을 사용한다. 640으로 축소하면
        이상영역이 사라져 탐지되지 않는다.
    """
    temp_path = save_upload(file)

    # 응답에 원본 파일명이 나오도록 유지한다
    original_name = file.filename or temp_path.name

    try:
        result = detector.detect_anomalies(
            image_path=temp_path,
            analysis_target=analysis_target,
            confidence=confidence,
            imgsz=imgsz,
        )

        for region in result["regions"]:
            region["fileName"] = original_name

        result["summary"]["fileName"] = original_name

        return JSONResponse({
            "success": True,
            "regions": result["regions"],
            "summary": result["summary"],
        })

    except ValueError as error:
        raise HTTPException(
            status_code=400, detail=str(error)
        )

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"{type(error).__name__}: {error}",
        )

    finally:
        cleanup([temp_path])


# ------------------------------------------------------------
# 여러 이미지 일괄 탐지
#
# 조각 30장을 한 번에 처리할 때 사용한다.
# CPU 기준 1~2분이 걸리므로 호출부에서
# 비동기 job으로 감싸는 것을 권장한다.
# ------------------------------------------------------------

@router.post("/detect-batch")
async def detect_batch(
    files: list[UploadFile] = File(...),
    source_indexes: list[int] = Form(...),
    analysis_target: str = Form("원본 조각"),
    confidence: float = Form(None),
    imgsz: int = Form(None),
):
    temp_paths = []
    all_regions = []
    summaries = []
    next_index = 1

    if len(files) != len(source_indexes):
        raise HTTPException(
            status_code=400,
            detail="files와 source_indexes 개수가 일치해야 합니다.",
        )

    try:
        for file, source_index in zip(files, source_indexes):
            temp_path = save_upload(file)
            temp_paths.append(temp_path)

            original_name = (
                file.filename or temp_path.name
            )

            try:
                result = detector.detect_anomalies(
                    image_path=temp_path,
                    analysis_target=analysis_target,
                    confidence=confidence,
                    imgsz=imgsz,
                    start_index=next_index,
                )

                for region in result["regions"]:
                    region["fileName"] = original_name
                    region["sourceIndex"] = source_index

                result["summary"]["fileName"] = (
                    original_name
                )
                result["summary"]["sourceIndex"] = source_index

                all_regions.extend(result["regions"])
                summaries.append(result["summary"])
                next_index = result["nextIndex"]

            except Exception as error:
                # 한 장이 실패해도 나머지는 계속 처리한다
               summaries.append({
                    "fileName": original_name,
                    "sourceIndex": source_index,
                    "analysisTarget": analysis_target,
                    "regionCount": 0,
                    "error": (
                        f"{type(error).__name__}: {error}"
                    ),
                })

        return JSONResponse({
            "success": True,
            "totalRegionCount": len(all_regions),
            "regions": all_regions,
            "summaries": summaries,
        })

    finally:
        cleanup(temp_paths)


# ------------------------------------------------------------
# 전문가용 1차 상태조사 문안 생성
#
# 탐지 좌표만으로는 GPT가 영상을 볼 수 없으므로
# 이미지를 다시 전송받아 박스를 그린 뒤 함께 전달한다.
#
# 소요 시간은 30초~2분이며 이미지 장수와
# 출력 길이에 따라 달라진다.
# ------------------------------------------------------------

@router.post("/report")
async def generate_report(
    regions: str = Form(...),
    artifact_type: str = Form(""),
    material: str = Form(""),
    report_style: str = Form("summary"),
    assembled: UploadFile = File(None),
    fragments: list[UploadFile] = File(None),
    rgb_images: list[UploadFile] = File(None),
):
    """
    regions
        탐지 결과 JSON 문자열.

        전문가가 검수한 결과(오탐 제외, userNote 수정)를
        그대로 보내면 문안에 반영된다.

    report_style
        summary  - PPT 삽입용 요약본, 1500자 내외, 영역 5건
        detailed - 공식 기록용 상세본, 9개 절, 영역 12건
    """
    if report_style not in ("summary", "detailed"):
        raise HTTPException(
            status_code=400,
            detail=(
                "report_style은 summary 또는 detailed여야 합니다."
            ),
        )
    if not config.OPENAI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail=(
                "OPENAI_API_KEY가 설정되지 않았습니다. "
                "문안 생성 기능을 사용할 수 없습니다."
            ),
        )

    try:
        region_list = json.loads(regions)

    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=400,
            detail=f"regions JSON 파싱 실패: {error}",
        )

    if not isinstance(region_list, list):
        raise HTTPException(
            status_code=400,
            detail="regions는 배열이어야 합니다.",
        )

    temp_paths = []

    try:
        assembled_path = None

        if assembled is not None:
            assembled_path = save_upload(assembled)
            temp_paths.append(assembled_path)

        fragment_paths = []

        for f in (fragments or [])[
            : config.LLM_MAX_IMAGES
        ]:
            p = save_upload(f)
            temp_paths.append(p)
            fragment_paths.append(p)

        rgb_paths = []

        for f in (rgb_images or [])[
            : config.LLM_MAX_IMAGES
        ]:
            p = save_upload(f)
            temp_paths.append(p)
            rgb_paths.append(p)

        print(
            f"[LLM] style={report_style} | "
            f"전체 {len(region_list)}건 | "
            f"결합본 {1 if assembled_path else 0} | "
            f"조각 {len(fragment_paths)} | "
            f"컬러 {len(rgb_paths)}"
        )

        result = reporter.generate_report(
            regions=region_list,
            assembled_path=assembled_path,
            fragment_paths=fragment_paths,
            rgb_paths=rgb_paths,
            artifact_type=artifact_type,
            material=material,
            style=report_style,
        )

        print(
            f"[LLM] 완료 | {result['charCount']}자 | "
            f"개별 서술 {result['detailCount']}건"
        )

        return JSONResponse({
            "success": True,
            **result,
        })

    except RuntimeError as error:
        raise HTTPException(
            status_code=503, detail=str(error)
        )

    except Exception as error:
        import traceback
        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail=f"{type(error).__name__}: {error}",
        )

    finally:
        cleanup(temp_paths)
