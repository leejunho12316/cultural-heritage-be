package com.aivle.conservation_backend.vca.gateway;

public record VcaAiAssessmentStage(
        String name,
        String status,
        Integer exitCode,
        String reason
) {
}
