package com.aivle.conservation_backend.vca.gateway;

import java.util.List;

public record VcaAiAssessmentRun(
        String runId,
        String assessmentId,
        String status,
        String currentStage,
        VcaAiAssessmentStageProgress currentStageProgress,
        List<VcaAiAssessmentStage> stages,
        String failureReason
) {

    // stages/failureReason 등이 아직 없는 시점(run 생성 직후)을 위한 축약 생성자.
    public VcaAiAssessmentRun(String runId, String assessmentId, String status) {
        this(runId, assessmentId, status, null, null, List.of(), null);
    }
}
