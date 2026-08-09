package com.aivle.conservation_backend.vca.gateway;

// vca-ai 엔진과의 통신 계약. 운영 빈은 RestClientVcaAiGateway, VcaService가 호출하는
// 유일한 진입점이며 AI 게이트웨이가 없는 데모 모드에서는 이 빈 자체가 주입되지 않는다.
public interface VcaAiGateway {

    // 업로드된 이미지 폴더로 새 assessment run을 시작한다. VcaService.createAiAssessmentRun에서 호출.
    VcaAiAssessmentRun createAssessmentRun(
            String assessmentId,
            String projectName,
            String inputImageFolder
    );

    // run의 현재 상태/단계를 폴링 조회. VcaService.syncRunWithAi에서 호출.
    VcaAiAssessmentRun getAssessmentStatus(String runId);

    // 실행 중인 run을 중지시킨다("분석 중지" 기능). VcaService.cancelRun에서 호출.
    VcaAiAssessmentRun cancelAssessmentRun(String runId);

    // 완료된 run의 최종 리포트(findings, RAG 근거 등)를 조회한다.
    VcaAiAssessmentReport getAssessmentReport(String runId);
}
