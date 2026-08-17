package com.aivle.conservation_backend.vca.dto;

import java.time.Instant;
import java.util.List;

public record RunResponse(
        String assessmentRunId,
        String artifactId,
        String status,
        int imageCount,
        Instant createdAt,
        Instant completedAt,
        String reportUrl,
        String currentStage,
        Integer progressPercent,
        List<Stage> stages,
        String failureReason
) {

    public record Stage(
            String name,
            String status,
            Integer exitCode,
            String reason
    ) {
    }
}
