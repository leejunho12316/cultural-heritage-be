package com.aivle.conservation_backend.vca.gateway;

import java.util.List;

public record VcaAiAssessmentReport(
        String runId,
        String assessmentId,
        String status,
        String summary,
        List<VcaAiAssessmentFinding> findings
) {
}
