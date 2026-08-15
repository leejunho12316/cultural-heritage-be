import re
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from app.services.assessment_input_validation import (
    SUPPORTED_IMAGE_SUFFIXES,
    is_valid_project_name,
    validate_input_image_folder,
    validate_project_name,
)
from app.services.assessment_models import (
    AssessmentFinding,
    AssessmentFindingBbox,
    AssessmentFindingCitation,
    AssessmentId,
    AssessmentReport,
    AssessmentRun,
    AssessmentRunId,
    ENGINE_OUTPUT_STAGES,
    InputImageFolder,
    ProjectName,
)
from app.services.remote_io import RemoteIoError, download
from app.services.vca_artifacts import load_vca_report
from app.services.vca_process import VcaRunFailedError, run_vca
from app.services.vca_progress import write_adapter_failure_progress
from app.services.vca_resume import prepare_resume
from app.services.vca_runtime_settings import (
    VcaRunMode,
    VcaRuntimeSettings,
    VcaRuntimeSettingsError,
    runtime_settings_from_env,
)


_RUN_PREFIX: Final = "vca-"
_RUN_PROJECT_SEPARATOR: Final = "~"
_ASSESSMENT_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


@dataclass(frozen=True, slots=True)
class InvalidAssessmentRunIdError(Exception):
    run_id: str

    def __str__(self) -> str:
        return f"Unknown VCA assessment run: {self.run_id}"


@dataclass(frozen=True, slots=True)
class InputImageUrl:
    file_name: str
    url: str


@dataclass(frozen=True, slots=True)
class InputImageDownloadError(Exception):
    reason: str

    def __str__(self) -> str:
        return f"Failed to download VCA assessment input images: {self.reason}"


def create_assessment_run(
    assessment_id: AssessmentId,
    project_name: ProjectName,
    input_image_folder: InputImageFolder | None = None,
    *,
    input_image_urls: tuple[InputImageUrl, ...] | None = None,
    resume_from_project_name: ProjectName | None = None,
) -> AssessmentRun:
    """run을 등록하고 파이프라인을 백그라운드로 실행한다.

    subprocess가 시작되는 즉시 반환하며, 호출자는 수십 분 걸리는 실제
    실행이 끝날 때까지 기다리지 않고 get_assessment_progress()로 단계별
    상태를 폴링한다.

    input_image_folder와 input_image_urls는 둘 중 정확히 하나만 채워진다.
    전자는 공유 마운트 경로가 있는 로컬 폴백(Spring이 이미 파일을 그
    경로에 갖다놓았다고 신뢰)이고, 후자는 S3 기반 저장소용으로, 여기서
    직접 각 URL을 이 프로세스의 로컬 임시 디렉터리로 내려받은 뒤 그
    디렉터리를 입력으로 쓴다(EFS 등 공유 볼륨이 필요 없다).

    resume_from_project_name은 Spring이 같은 artifact의 가장 최근 FAILED
    run(이미지 구성이 이번 run과 정확히 같음을 이미 확인한 뒤)을 넘겨줄 때만
    채워진다. 그 run의 완료된 스테이지 산출물을 이 run의 project_name
    아래로 미리 복사해두고 vca_v2를 --resume-from-stage와 함께 실행해
    이미 끝난 스테이지를 다시 돌리지 않는다. 전제가 하나라도 안 맞으면
    (리시트 없음/파싱 실패/산출물 유실/복사 실패 등) 조용히 평소처럼
    처음부터 전체 실행으로 폴백한다 - 이어가기는 순수 최적화일 뿐이다.
    """
    settings = runtime_settings_from_env()
    validate_project_name(project_name)
    if input_image_urls:
        input_directory = _download_input_images(
            project_name, input_image_urls, settings.shared_storage_root
        )
    else:
        input_directory = validate_input_image_folder(
            input_image_folder, settings.shared_storage_root
        )
    run = AssessmentRun(
        run_id=AssessmentRunId(
            f"{_RUN_PREFIX}{assessment_id}{_RUN_PROJECT_SEPARATOR}{project_name}"
        ),
        assessment_id=assessment_id,
        project_name=project_name,
    )
    resume_from_stage = prepare_resume(project_name, resume_from_project_name, settings)
    if resume_from_stage is None:
        _clear_project_output(assessment_id, project_name, settings)
    _launch_background(run, input_directory, settings, resume_from_stage)
    return run


# input_image_urls로 받은 이미지들을 이 프로세스의 로컬 임시 디렉터리로
# 내려받는다(project_name별로 새로 만들고, 이전 내용이 있으면 지운다).
# create_assessment_run()이 inputImageFolder 대신 inputImageUrls를 받았을
# 때만 호출한다.
def _download_input_images(
    project_name: ProjectName,
    input_image_urls: tuple[InputImageUrl, ...],
    shared_storage_root: Path,
) -> Path:
    shared_root = shared_storage_root.resolve()
    input_directory = (shared_root / project_name / "input").resolve()
    if not input_directory.is_relative_to(shared_root):
        raise InputImageDownloadError("resolved input directory escapes the shared storage root")
    try:
        if input_directory.exists():
            shutil.rmtree(input_directory)
        input_directory.mkdir(parents=True, exist_ok=True)
        for image in input_image_urls:
            file_name = Path(image.file_name).name
            if Path(file_name).suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
                raise InputImageDownloadError(f"unsupported image file extension: {file_name}")
            download(image.url, input_directory / file_name)
    except RemoteIoError as error:
        raise InputImageDownloadError(str(error)) from error
    return input_directory


def _launch_background(
    run: AssessmentRun,
    input_directory: Path,
    settings: VcaRuntimeSettings,
    resume_from_stage: str | None,
) -> None:
    """실제 파이프라인 작업을 요청 스레드가 아닌 별도 스레드에서 시작한다.

    테스트에서 실제 백그라운드 스레드와의 경합 없이 동기적으로(inline)
    실행되도록 monkeypatch할 수 있는 지점으로 분리해 두었다.
    """
    thread = threading.Thread(
        target=_run_vca_and_record_failure,
        args=(run, input_directory, settings, resume_from_stage),
        daemon=True,
    )
    thread.start()


# 파이프라인을 실행하고, 실패 시 어댑터 차원에서 진행 상태를 기록한다.
# _launch_background()가 생성한 백그라운드 스레드의 진입점이다.
def _run_vca_and_record_failure(
    run: AssessmentRun,
    input_directory: Path,
    settings: VcaRuntimeSettings,
    resume_from_stage: str | None,
) -> None:
    try:
        run_vca(run, input_directory, settings, resume_from_stage)
    except (VcaRunFailedError, VcaRuntimeSettingsError) as error:
        # 파이프라인이 자체 progress.json을 쓸 만큼도 진행되지 못했거나
        # (또는 `uv` 실행 파일 부재처럼 파이프라인 외부 원인으로) 실패한
        # 경우이므로, 폴러가 "running" 상태로 계속 남아있지 않도록 여기서
        # 직접 실패를 기록한다.
        write_adapter_failure_progress(run, settings, str(error))


# run_id 문자열("vca-{assessmentId}~{projectName}")을 분해해 AssessmentRun을
# 복원한다. 형식이 어긋나면 InvalidAssessmentRunIdError를 던진다.
def get_assessment_run(run_id: str) -> AssessmentRun:
    run_values = run_id.removeprefix(_RUN_PREFIX)
    assessment_id, separator, project_name = run_values.partition(_RUN_PROJECT_SEPARATOR)
    if (
        run_values == run_id
        or not separator
        or _ASSESSMENT_ID_PATTERN.fullmatch(assessment_id) is None
        or not is_valid_project_name(project_name)
    ):
        raise InvalidAssessmentRunIdError(run_id)
    return AssessmentRun(
        run_id=AssessmentRunId(run_id),
        assessment_id=AssessmentId(assessment_id),
        project_name=ProjectName(project_name),
    )


# 엔진 산출물을 읽어(load_vca_report) 서비스 계층의 AssessmentReport로
# 변환한다. 라우터의 get_run_report()가 호출한다.
def get_assessment_report(run: AssessmentRun) -> AssessmentReport:
    settings = runtime_settings_from_env()
    artifacts = load_vca_report(
        settings.engine_root,
        str(run.project_name),
        is_dry_run=settings.run_mode is VcaRunMode.DRY_RUN,
    )
    return AssessmentReport(
        run=run,
        summary=artifacts.summary,
        findings=tuple(
            AssessmentFinding(
                category=finding.category,
                severity=finding.severity,
                message=finding.message,
                candidate_id=finding.candidate_id,
                image_id=finding.image_id,
                concept_family=finding.concept_family,
                descriptor=finding.descriptor,
                citations=tuple(
                    AssessmentFindingCitation(
                        citation_id=citation.citation_id,
                        source_citation=citation.source_citation,
                        page_number=citation.page_number,
                    )
                    for citation in finding.citations
                ),
                bbox=None
                if finding.bbox is None
                else AssessmentFindingBbox(
                    x_min=finding.bbox.x_min,
                    y_min=finding.bbox.y_min,
                    x_max=finding.bbox.x_max,
                    y_max=finding.bbox.y_max,
                ),
                polygons=finding.polygons,
            )
            for finding in artifacts.findings
        ),
        rag=artifacts.rag,
    )


# 재실행 전에 이전 run이 각 스테이지에 남긴 산출물 디렉터리를 삭제한다.
# create_assessment_run()에서 파이프라인을 다시 시작하기 직전에 호출된다.
def _clear_project_output(
    assessment_id: AssessmentId,
    project_name: ProjectName,
    settings: VcaRuntimeSettings,
) -> None:
    output_root = settings.engine_root / "output"
    for stage in ENGINE_OUTPUT_STAGES:
        stage_project_directory = output_root / stage / str(project_name)
        if not stage_project_directory.exists():
            continue
        try:
            shutil.rmtree(stage_project_directory)
        except OSError as error:
            raise VcaRunFailedError(
                assessment_id, f"failed to clear previous output for {stage}"
            ) from error
