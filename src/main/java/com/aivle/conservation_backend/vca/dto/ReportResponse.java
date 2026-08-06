package com.aivle.conservation_backend.vca.dto;

import java.time.Instant;
import java.util.List;
import java.util.Map;

public record ReportResponse(
        String assessmentRunId,
        String artifactId,
        String status,
        Instant generatedAt,
        Summary summary,
        List<Finding> findings,
        List<Recommendation> recommendations,
        List<Image> images,
        PotteryInspection potteryInspection,
        PotteryInspectionStatus potteryInspectionStatus
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

    public record PotteryInspection(
            String moduleVersion,
            String inspectionText,
            String summary,
            boolean humanReviewRecommended,
            Map<String, Object> detail
    ) {
    }

    public record PotteryInspectionStatus(
            boolean applicable,
            String status,
            boolean retryable,
            String failureMessage,
            Instant lastAttemptedAt
    ) {
    }
}
