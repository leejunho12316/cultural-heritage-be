package com.aivle.conservation_backend.vca;

import java.time.Instant;
import java.util.List;
import java.util.Map;

public final class VcaResponses {

    private VcaResponses() {
    }

    public record ArtifactCollection(List<ArtifactSummary> items) {
    }

    public record ArtifactSummary(
            String artifactId,
            String displayName,
            String thumbnailUrl,
            Run latestRun,
            int runCount,
            Instant updatedAt
    ) {
    }

    public record ArtifactDetail(
            String artifactId,
            String displayName,
            String status,
            List<Image> uploadedImages,
            List<Run> runs,
            Instant createdAt,
            Instant updatedAt
    ) {
    }

    public record PresignImage(
            String imageId,
            String status,
            String uploadMode,
            String uploadUrl,
            String method,
            Map<String, String> requiredHeaders,
            Instant expiresAt
    ) {
    }

    public record Image(
            String imageId,
            String fileName,
            String contentType,
            long sizeBytes,
            String status,
            String imageUrl,
            Instant createdAt,
            Instant uploadedAt
    ) {
    }

    public record Run(
            String assessmentRunId,
            String artifactId,
            String status,
            int imageCount,
            Instant createdAt,
            Instant completedAt,
            String reportUrl
    ) {
    }

    public record Report(
            String assessmentRunId,
            String artifactId,
            String status,
            Instant generatedAt,
            ReportSummary summary,
            List<Finding> findings,
            List<Recommendation> recommendations,
            List<ReportImage> images
    ) {
    }

    public record ReportSummary(
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

    public record ReportImage(
            String imageId,
            String fileName,
            String downloadUrl
    ) {
    }

    public record PdfJob(
            String jobId,
            String assessmentRunId,
            String status,
            Instant createdAt,
            Instant updatedAt,
            String downloadUrl
    ) {
    }

    public record ErrorEnvelope(ErrorDetail error) {
    }

    public record ErrorDetail(String code, String message) {
    }
}
