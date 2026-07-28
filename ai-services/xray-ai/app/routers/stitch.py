"""
X-ray 조각 결합(스티칭) 라우터

담당: 조각 결합 파트

이 파일만 수정하면 되므로 결함 탐지 파트와
git 충돌이 나지 않는다.

아래는 골격이며 실제 구현으로 교체하면 된다.
main.py는 수정할 필요가 없다.


[규칙 세 가지]

1. 경로는 이 파일에서 정의한다.
   접두사가 필요하면 main.py의 include_router에서
   prefix="/stitch"를 지정한다.

2. 응답 필드는 camelCase를 쓴다.
   Spring DTO 매핑이 일관되도록 하기 위함이다.
   예: fileName, regionCount (O)
       file_name, region_count (X)

3. 업로드 파일은 app.utils.save_upload를 쓰고
   finally에서 cleanup으로 반드시 삭제한다.
   서버는 파일을 보관하지 않는다.


[변환행렬 반환 요청]

결합 결과에 조각별 변환행렬을 포함해주면
원본 조각의 탐지 좌표를 결합본 좌표로 투영할 수 있다.

OpenCV로 스티칭할 때 estimateAffinePartial2D나
findHomography가 이미 계산하는 값이다.

    {
      "fragments": [
        {
          "fileName": "img001.jpg",
          "transform": [[a, b, tx], [c, d, ty]]
        }
      ]
    }

이 값이 있으면 결합본에서만 탐지된 영역이
실제 손상인지 결합 과정의 인공물인지 구분할 수 있다.
"""

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.responses import JSONResponse

from app.utils import cleanup, save_upload


router = APIRouter(tags=["stitch"])


@router.post("/run")
async def run_stitch(
    files: list[UploadFile] = File(...),
    artifact_type: str = Form(""),
):
    """
    X-ray 조각들을 결합한다.

    TODO: 실제 스티칭 로직으로 교체

    예상 응답 구조

        {
          "success": true,
          "compositeImage": "base64 또는 저장 경로",
          "fragments": [
            {
              "fileName": "img001.jpg",
              "transform": [[a, b, tx], [c, d, ty]],
              "matched": true
            }
          ],
          "summary": {
            "totalFragments": 30,
            "matchedCount": 27,
            "canvasWidth": 5648,
            "canvasHeight": 4237
          }
        }
    """
    temp_paths = []

    try:
        for file in files:
            temp_paths.append(save_upload(file))

        # ----------------------------------------------------
        # TODO: 스티칭 구현
        #
        # from app.services import stitcher
        # result = stitcher.run(temp_paths)
        # ----------------------------------------------------

        raise HTTPException(
            status_code=501,
            detail="스티칭 기능이 아직 구현되지 않았습니다.",
        )

    finally:
        cleanup(temp_paths)
