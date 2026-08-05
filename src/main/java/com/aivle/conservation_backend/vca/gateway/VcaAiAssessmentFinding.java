package com.aivle.conservation_backend.vca.gateway;

public record VcaAiAssessmentFinding(
        String category,
        String severity,
        String message
) {
}
