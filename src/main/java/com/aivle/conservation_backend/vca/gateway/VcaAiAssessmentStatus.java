package com.aivle.conservation_backend.vca.gateway;

public record VcaAiAssessmentStatus(
        String runId,
        String assessmentId,
        String status
) {
}
