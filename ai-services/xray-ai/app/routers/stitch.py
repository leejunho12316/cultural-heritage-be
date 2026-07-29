"""
X-ray 조각 결합(스티칭) 라우터

담당: 조각 결합 파트

이 파일만 수정하면 되므로 결함 탐지 파트와
git 충돌이 나지 않는다.


[규칙 세 가지 — 준수 확인]

1. 경로는 이 파일에서 정의한다.
   접두사는 main.py의 include_router에서 prefix="/stitch"로
   이미 지정되어 있다. 따라서 여기서는 "/run"만 쓰고
   최종 경로는 /stitch/run 이 된다.
   이 파일에 다시 prefix를 주면 /stitch/stitch/run 이 되므로
   APIRouter에 prefix를 넣지 않는다.

2. 응답 필드는 camelCase를 쓴다.
   Spring DTO 매핑이 일관되도록 하기 위함이다.

3. 업로드 파일은 app.utils.save_upload를 쓰고
   finally에서 cleanup으로 반드시 삭제한다.
   서버는 파일을 보관하지 않는다.


[변환행렬]

fragments[].transform 에 조각별 2x3 affine 행렬을 담는다.

    [[a, b, tx],
     [c, d, ty]]

원본 조각 좌표 (x, y)를 결합본 좌표로 옮기려면

    X = a*x + b*y + tx
    Y = c*x + d*y + ty

이 값이 있어야 결합본에서만 탐지된 영역이
실제 손상인지 결합 과정의 인공물인지 구분할 수 있다.


[소요 시간]

조각 수와 설정에 따라 수 분이 걸린다.
결함 탐지의 /detect-batch(조각 30장 1~2분)와 마찬가지로
동기 호출이므로 Spring 타임아웃을 넉넉히 잡아야 한다.
application.yaml의 xray.ai.stitch-timeout-seconds 참조.
"""

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.responses import JSONResponse

from app.services import stitcher
from app.utils import cleanup, save_upload

router = APIRouter(tags=["stitch"])


# ------------------------------------------------------------
# 엔진 준비 상태 확인
#
# 결합은 수 분에서 수십 분이 걸린다. 설정 파일 하나가 없어서
# 한참 뒤에 실패하면 시간 낭비가 크므로, 실행 전에 상태를
# 확인할 수 있게 둔다.
#
# 서비스 전체 상태는 main.py의 /health가 담당한다.
# 결합 전용 진단은 여기에 두어 main.py를 건드리지 않는다.
# ------------------------------------------------------------


@router.get("/health")
def stitch_health():
    """결합 엔진이 실행 가능한 상태인지 확인한다."""
    return JSONResponse(stitcher.check_engine())


@router.post("/run")
async def run_stitch(
    files: list[UploadFile] = File(...),
    color_files: list[UploadFile] = File(None),
    artifact_id: str = Form(...),
    config_name: str = Form(None),
):
    """
    X-ray 조각들을 결합한다.

    files
        X-ray 조각 이미지. 최소 1장.

    color_files
        컬러 기준 이미지. 엔진이 컬러-X-ray 대응으로
        조각 배치를 잡는 데 사용한다.

    artifact_id
        유물 식별자. 결합 엔진의 매핑 파일에 등록된
        값이어야 한다. 등록되지 않은 값을 보내면
        엔진이 대상을 찾지 못해 실패한다.

    config_name
        결합 설정 이름. 미지정 시 서비스 기본값.


    응답 구조

        {
          "success": true,
          "compositeImage": "<base64 PNG>",
          "compositeFormat": "png",
          "fragments": [
            {
              "fileName": "img001.jpg",
              "transform": [[a, b, tx], [c, d, ty]],
              "matched": true,
              "cropBBoxXYWH": [x, y, w, h],
              "subfragmentIndex": 0
            }
          ],
          "summary": {
            "artifactId": "A-001",
            "totalFragments": 30,
            "matchedCount": 27,
            "canvasWidth": 5648,
            "canvasHeight": 4237,
            "previewScale": 1.0
          }
        }

    compositeImage는 base64다. 서버가 파일을 보관하지 않으므로
    경로를 반환해도 Spring이 읽을 수 없기 때문이다.
    좌표(canvasWidth/Height, transform)는 항상 원본 해상도
    기준이며, 반환 이미지가 축소된 경우 previewScale로
    환산한다.
    """
    temp_paths = []

    try:
        xray_paths = []
        xray_names = []

        for file in files:
            path = save_upload(file)
            temp_paths.append(path)
            xray_paths.append(path)

            # save_upload는 {uuid}.jpg 로 저장하므로
            # 원본명을 따로 넘겨야 layout.json의 fileName이
            # 조각과 이어진다.
            xray_names.append(file.filename or path.name)

        color_paths = []
        color_names = []

        for file in color_files or []:
            # 프론트가 빈 파트를 보내는 경우가 있어 걸러낸다
            if not file.filename:
                continue

            path = save_upload(file)
            temp_paths.append(path)
            color_paths.append(path)
            color_names.append(file.filename)

        print(
            f"[STITCH] artifactId={artifact_id} | "
            f"조각 {len(xray_paths)} | "
            f"컬러 {len(color_paths)}"
        )

        result = stitcher.run(
            xray_paths=xray_paths,
            color_paths=color_paths,
            artifact_id=artifact_id,
            config_name=config_name,
            xray_names=xray_names,
            color_names=color_names,
        )

        return JSONResponse(
            {
                "success": True,
                **result,
            }
        )

    except stitcher.StitchError as error:
        # 엔진 실패는 이 서비스의 버그가 아니라
        # 입력·설정 문제인 경우가 대부분이므로 502로 구분한다.
        raise HTTPException(status_code=502, detail=str(error))

    except HTTPException:
        # save_upload가 올린 400/413은 그대로 통과시킨다
        raise

    except Exception as error:
        import traceback

        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail=f"{type(error).__name__}: {error}",
        )

    finally:
        cleanup(temp_paths)
