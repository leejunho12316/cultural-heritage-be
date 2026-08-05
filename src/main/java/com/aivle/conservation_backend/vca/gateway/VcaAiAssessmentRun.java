package com.aivle.conservation_backend.vca.gateway;

public record VcaAiAssessmentRun(
        String runId,
        String assessmentId,
        String status
) {
}
