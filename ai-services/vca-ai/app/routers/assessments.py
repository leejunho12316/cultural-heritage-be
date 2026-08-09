from typing import Literal

from fastapi import APIRouter, HTTPException, status

from app.schemas import (
    AssessmentFindingBboxResponse,
    AssessmentFindingCitationResponse,
    AssessmentFindingResponse,
    AssessmentReportResponse,
    AssessmentStageResponse,
    RagArtifactsResponse,
    RagEvidenceRowResponse,
    RagQueryResponse,
    RagRetrievalResultResponse,
    RagVisualConceptCardResponse,
    AssessmentRunCreateRequest,
    AssessmentRunResponse,
)
from app.services.assessment_models import AssessmentProgress
from app.services.assessment_runs import (
    AssessmentId,
    InputImageFolder,
    AssessmentReport,
    AssessmentRun,
    InvalidAssessmentRunIdError,
    ProjectName,
    VcaRunFailedError,
    VcaRuntimeSettingsError,
    cancel_run,
    create_assessment_run,
    get_assessment_progress,
    get_assessment_report,
    get_assessment_run,
)
from app.services.assessment_input_validation import (
    InvalidInputImageFolderError,
    InvalidProjectNameError,
)
from app.services.vca_artifacts import VcaReportArtifactError


router = APIRouter(prefix="/internal/vca", tags=["internal-vca"])


# VCA 분석 실행을 생성하는 엔드포인트. Spring 백엔드가 이미지 업로드 후
# 호출하며, 입력값 오류는 400, 파이프라인/설정 오류는 502로 매핑한다.
@router.post(
    "/assessment-runs",
    response_model=AssessmentRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_run(request: AssessmentRunCreateRequest) -> AssessmentRunResponse:
    try:
        run = create_assessment_run(
            AssessmentId(request.assessmentId),
            ProjectName(request.projectName),
            InputImageFolder(request.inputImageFolder),
        )
    except (InvalidInputImageFolderError, InvalidProjectNameError) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
    except (VcaRunFailedError, VcaRuntimeSettingsError) as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error
    return _run_response(run, get_assessment_progress(run))


# 실행 중인 assessment run의 현재 상태를 조회하는 엔드포인트.
# Spring 백엔드가 주기적으로 폴링한다.
@router.get("/assessment-runs/{run_id}", response_model=AssessmentRunResponse)
def get_run_status(run_id: str) -> AssessmentRunResponse:
    run = _known_run(run_id)
    return _run_response(run, get_assessment_progress(run))


# 실행 중인 파이프라인에 취소를 요청하는 엔드포인트.
# cancel_run()의 반환값(취소 대상 존재 여부)은 응답에 반영하지 않고 무시한다.
@router.post(
    "/assessment-runs/{run_id}/cancel",
    response_model=AssessmentRunResponse,
)
def cancel_run_endpoint(run_id: str) -> AssessmentRunResponse:
    run = _known_run(run_id)
    _ = cancel_run(run_id)
    return _run_response(run, get_assessment_progress(run))


# 완료된 assessment run의 최종 리포트를 조회하는 엔드포인트.
# 산출물 로딩 실패는 502로 매핑한다.
@router.get(
    "/assessment-runs/{run_id}/report",
    response_model=AssessmentReportResponse,
)
def get_run_report(run_id: str) -> AssessmentReportResponse:
    try:
        report = get_assessment_report(_known_run(run_id))
    except (VcaReportArtifactError, VcaRuntimeSettingsError) as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error
    return _report_response(report)


# run_id 문자열을 AssessmentRun으로 파싱하고, 형식이 잘못되었으면 404로
# 변환한다. 각 라우트 핸들러에서 공통으로 사용된다.
def _known_run(run_id: str) -> AssessmentRun:
    try:
        return get_assessment_run(run_id)
    except InvalidAssessmentRunIdError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error


# AssessmentRun/AssessmentProgress를 API 응답 DTO(AssessmentRunResponse)로
# 변환한다.
def _run_response(
    run: AssessmentRun, progress: AssessmentProgress
) -> AssessmentRunResponse:
    return AssessmentRunResponse(
        runId=run.run_id,
        assessmentId=run.assessment_id,
        status=_status_literal(progress.status),
        currentStage=progress.current_stage,
        failureReason=progress.failure_reason,
        stages=tuple(
            AssessmentStageResponse(
                name=stage.name,
                status=stage.status,
                exitCode=stage.exit_code,
                reason=stage.reason,
            )
            for stage in progress.stages
        ),
    )


def _status_literal(status_value: str) -> Literal["RUNNING", "COMPLETED", "FAILED"]:
    match status_value:
        case "completed":
            return "COMPLETED"
        case "failed":
            return "FAILED"
        case _:
            return "RUNNING"


# 완료된 AssessmentReport를 API 응답 DTO(AssessmentReportResponse)로
# 변환한다. RAG 산출물은 _rag_artifacts_response()에 위임한다.
def _report_response(report: AssessmentReport) -> AssessmentReportResponse:
    return AssessmentReportResponse(
        runId=report.run.run_id,
        assessmentId=report.run.assessment_id,
        status="COMPLETED",
        summary=report.summary,
        findings=tuple(
            AssessmentFindingResponse(
                category=finding.category,
                severity=finding.severity,
                message=finding.message,
                candidateId=finding.candidate_id,
                imageId=finding.image_id,
                conceptFamily=finding.concept_family,
                descriptor=finding.descriptor,
                citations=tuple(
                    AssessmentFindingCitationResponse(
                        citationId=citation.citation_id,
                        sourceCitation=citation.source_citation,
                        pageNumber=citation.page_number,
                    )
                    for citation in finding.citations
                ),
                bbox=None
                if finding.bbox is None
                else AssessmentFindingBboxResponse(
                    xMin=finding.bbox.x_min,
                    yMin=finding.bbox.y_min,
                    xMax=finding.bbox.x_max,
                    yMax=finding.bbox.y_max,
                ),
                polygon=finding.polygon,
            )
            for finding in report.findings
        ),
        ragArtifacts=_rag_artifacts_response(report),
    )


# RAG 산출물(optional)을 응답 DTO로 변환한다. RAG 스테이지 산출물이 없으면
# None을 반환해 ragArtifacts 필드를 생략한다.
def _rag_artifacts_response(report: AssessmentReport) -> RagArtifactsResponse | None:
    rag = report.rag
    if rag is None:
        return None
    return RagArtifactsResponse(
        schema_=rag.schema,
        queryCount=len(rag.queries),
        retrievalResultCount=len(rag.retrieval_results),
        evidenceRowCount=len(rag.evidence_rows),
        visualConceptCardCount=len(rag.visual_concept_cards),
        queries=tuple(
            RagQueryResponse(
                lane=query.lane,
                promptText=query.prompt_text,
                queryId=query.query_id,
            )
            for query in rag.queries
        ),
        retrievalResults=tuple(
            RagRetrievalResultResponse(
                chunkId=result.chunk_id,
                citationId=result.citation_id,
                lane=result.lane,
                matchedTerms=result.matched_terms,
                pageNumber=result.page_number,
                promptText=result.prompt_text,
                queryId=result.query_id,
                rank=result.rank,
                score=result.score,
                snippetText=result.snippet_text,
                sourceCitation=result.source_citation,
            )
            for result in rag.retrieval_results
        ),
        evidenceRows=tuple(
            RagEvidenceRowResponse(
                evidenceState=row.evidence_state,
                lane=row.lane,
                matchedCitationIds=row.matched_citation_ids,
                promptText=row.prompt_text,
                queryId=row.query_id,
                ragParentCandidateId=row.rag_parent_candidate_id,
                topCitationId=row.top_citation_id,
                topRetrievalScore=row.top_retrieval_score,
            )
            for row in rag.evidence_rows
        ),
        visualConceptCards=tuple(
            RagVisualConceptCardResponse(
                conceptCardId=card.concept_card_id,
                conceptFamily=card.concept_family,
                contextTerms=card.context_terms,
                descriptorTerms=card.descriptor_terms,
                materialTerms=card.material_terms,
                provenanceStrength=card.provenance_strength,
                ragParentCandidateId=card.rag_parent_candidate_id,
                rawRetrievedSentence=card.raw_retrieved_sentence,
                retrievalScore=card.retrieval_score,
                sourceCitationIds=card.source_citation_ids,
            )
            for card in rag.visual_concept_cards
        ),
    )
