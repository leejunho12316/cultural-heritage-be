import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from app.services.assessment_models import InputImageFolder, ProjectName


_PROJECT_NAME_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
SUPPORTED_IMAGE_SUFFIXES: Final = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
)


@dataclass(frozen=True, slots=True)
class InvalidProjectNameError(Exception):
    project_name: str

    def __str__(self) -> str:
        return f"Invalid VCA project name: {self.project_name}"


@dataclass(frozen=True, slots=True)
class InvalidInputImageFolderError(Exception):
    input_image_folder: str
    reason: str

    def __str__(self) -> str:
        return f"Invalid VCA input image folder: {self.reason}"


def is_valid_project_name(project_name: str) -> bool:
    return _PROJECT_NAME_PATTERN.fullmatch(project_name) is not None


def validate_project_name(project_name: ProjectName) -> None:
    if not is_valid_project_name(project_name):
        raise InvalidProjectNameError(str(project_name))


# 업로드 폴더가 공유 스토리지 루트 하위에 있고, 실제 존재하며, 지원 이미지
# 확장자를 가진 파일을 하나 이상 포함하는지 검증한다.
# create_assessment_run()에서 파이프라인 실행 전 호출된다.
def validate_input_image_folder(
    input_image_folder: InputImageFolder,
    shared_storage_root: Path,
) -> Path:
    shared_root = shared_storage_root.resolve()
    input_directory = Path(input_image_folder).resolve()
    if not input_directory.is_relative_to(shared_root):
        raise InvalidInputImageFolderError(
            str(input_image_folder), "folder must be under VCA_SHARED_STORAGE_ROOT"
        )
    if not input_directory.is_dir():
        raise InvalidInputImageFolderError(str(input_image_folder), "folder does not exist")
    if not any(
        child.is_file() and child.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
        for child in input_directory.iterdir()
    ):
        raise InvalidInputImageFolderError(
            str(input_image_folder), "folder contains no supported images"
        )
    return input_directory
