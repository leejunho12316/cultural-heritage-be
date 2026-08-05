package com.aivle.conservation_backend.vca.dto;

import java.time.Instant;
import java.util.List;

public record ReportResponse(
        String assessmentRunId,
        String artifactId,
        String status,
        Instant generatedAt,
        Summary summary,
        List<Finding> findings,
        List<Recommendation> recommendations,
        List<Image> images
) {

    public record Summary(
            String overallCondition,
            String riskLevel,
            String headline,
            String description
    ) {
    }

    public record Finding(
            String findingId,
            String category,
            String severity,
            String title,
            String description,
            double confidence,
            String imageId
    ) {
    }

    public record Recommendation(
            String recommendationId,
            String priority,
            String title,
            String description
    ) {
    }

    public record Image(
            String imageId,
            String fileName,
            String downloadUrl
    ) {
    }
}
