package com.aivle.conservation_backend.xray_api.dto;

import java.time.Instant;
import java.util.List;
import java.util.Map;

public final class XrayWorkflowDtos {

    private XrayWorkflowDtos() {
    }

    public record DetectRequest(Double confidence) {
    }

    public record DefectItem(
            Long id,
            String originType,
            Map<String, Object> geometry,
            String reviewDecision,
            Instant createdAt,
            Instant updatedAt
    ) {
    }

    public record DefectListResponse(
            String jobId,
            String artifactId,
            String status,
            String assembledFinalUrl,
            List<DefectItem> defects
    ) {
    }

    public record DefectReviewUpdate(
            Long id,
            String reviewDecision
    ) {
    }

    public record DefectReviewRequest(List<DefectReviewUpdate> defects) {
    }

    public record ReportGenerateRequest(
            String artifactType,
            String material,
            String reportStyle
    ) {
    }

    public record ReportTextRequest(String reportText) {
    }

    public record ReportTextResponse(
            String jobId,
            String artifactId,
            String reportText,
            String status
    ) {
    }

    public record CompleteResponse(
            String jobId,
            String artifactId,
            String status,
            String defectResultUrl
    ) {
    }
}
