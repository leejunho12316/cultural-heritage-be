package com.aivle.conservation_backend.vca.gateway;

public interface VcaAiGateway {

    VcaAiAssessmentRun createAssessmentRun(
            String assessmentId,
            String projectName,
            String inputImageFolder
    );

    VcaAiAssessmentRun getAssessmentStatus(String runId);

    VcaAiAssessmentReport getAssessmentReport(String runId);
}
