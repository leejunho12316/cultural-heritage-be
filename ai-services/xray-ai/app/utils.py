"""
라우터 공용 유틸리티

결함 탐지와 조각 결합 양쪽에서 쓰는 기능을 모은다.
각자 라우터에 중복 구현하지 않도록 여기에 둔다.
"""

import shutil
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app import config


def save_upload(file: UploadFile) -> Path:
    """
    업로드 파일을 임시 경로에 저장하고 경로를 반환한다.

    호출한 쪽에서 반드시 finally 블록에서
    unlink(missing_ok=True)로 삭제해야 한다.
    서버는 업로드 파일을 보관하지 않는다.

    Raises
    ------
    HTTPException
        400: 지원하지 않는 확장자
        413: 크기 초과
    """
    suffix = Path(file.filename or "").suffix.lower()

    if suffix not in config.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"지원하지 않는 형식입니다: {suffix}. "
                f"허용: {sorted(config.ALLOWED_EXTENSIONS)}"
            ),
        )

    temp_path = (
        config.UPLOAD_DIR
        / f"{uuid.uuid4().hex}{suffix}"
    )

    with temp_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    size = temp_path.stat().st_size

    if size > config.MAX_UPLOAD_SIZE:
        temp_path.unlink(missing_ok=True)

        raise HTTPException(
            status_code=413,
            detail=(
                f"파일이 너무 큽니다: "
                f"{size // 1024 // 1024}MB"
            ),
        )

    return temp_path


def cleanup(paths) -> None:
    """임시 파일을 정리한다."""
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
