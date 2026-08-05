package com.aivle.conservation_backend.vca.gateway;

public interface VcaAiGateway {

    VcaAiAssessmentRun createAssessmentRun(
            String assessmentId,
            String projectName,
            String inputImageFolder
    );

    VcaAiAssessmentStatus getAssessmentStatus(String runId);

    VcaAiAssessmentReport getAssessmentReport(String runId);
}
