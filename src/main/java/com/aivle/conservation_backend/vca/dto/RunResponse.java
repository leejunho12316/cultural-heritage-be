package com.aivle.conservation_backend.vca.dto;

import java.time.Instant;

public record RunResponse(
        String assessmentRunId,
        String artifactId,
        String status,
        int imageCount,
        Instant createdAt,
        Instant completedAt,
        String reportUrl
) {
}
