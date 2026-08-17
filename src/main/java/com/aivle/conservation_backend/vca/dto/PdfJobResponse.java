package com.aivle.conservation_backend.vca.dto;

import java.time.Instant;

public record PdfJobResponse(
        String jobId,
        String assessmentRunId,
        String status,
        Instant createdAt,
        Instant updatedAt,
        String downloadUrl
) {
}
